"""Detailed (multi-section) report.

Port of GPT Researcher's backend DetailedReport, which is not shipped in the pip package: one broad
research pass, then the topic is split into subtopics, each researched and written as its own section,
then stitched together with an introduction, table of contents, conclusion and references.

Unlike upstream, the subtopics are researched all at once (that is mostly waiting for the network) while
the introduction is written; their sections are still written one after another, so that each sees the
ones before it and does not repeat them. The sub-researchers share one visited_urls set, so no page is
read twice and the search part's per-domain cap holds across the whole research.

The whole report is at most prompts.REPORT_WORDS words: upstream wanted every section at least
TOTAL_WORDS long, which made 7-9 thousand words of four sections. Here the sections share what the
introduction, the conclusion, the contents and the references leave (section_words()).
"""

import asyncio
import copy
import logging

from gpt_researcher import GPTResearcher
from gpt_researcher.actions import generate_draft_section_titles
from gpt_researcher.utils.llm import construct_subtopics

from my_browser.common import phases
from my_browser.common.text import MARKDOWN_LINK
from my_browser.worker.prompts import CONCLUSION_WORDS, INTRO_WORDS, OVERSHOOT, REPORT_WORDS

logger = logging.getLogger(__name__)

# The subtopic list is the report's structure, so the writer model still makes it, with a short answer.
SUBTOPICS_TOKENS = 2000
# Draft section titles only find what earlier sections already wrote, so FAST_LLM makes them. On the
# writer model they cost ≈ 3 ₽ a Pro report, and one such call looped up to its 32k-token limit (≈ 5 ₽, 4 min).
TITLES_TOKENS, MAX_TITLES = 1000, 8
# The table of contents and the reference list count in the report's words too: about 200-400 together.
CONTENTS_WORDS = 450


def section_words(sections: int) -> int:
    """How long each section is asked to be: what the introduction, conclusion, contents and references leave
    of the report's limit, shared by the sections, with room for the writer to go over (OVERSHOOT)."""
    left = REPORT_WORDS - INTRO_WORDS - CONCLUSION_WORDS - CONTENTS_WORDS
    return max(300, int(left / OVERSHOOT / max(1, sections)) // 50 * 50)


def _limited(cfg, tokens: int, fast: bool = False):
    """A copy of the run's config whose "smart" calls answer in at most `tokens` tokens, on FAST_LLM if `fast`."""
    limited = copy.copy(cfg)
    if fast:
        limited.smart_llm_model, limited.smart_llm_provider = cfg.fast_llm_model, cfg.fast_llm_provider
    limited.smart_token_limit = tokens
    # DeepSeek reads only extra_body.max_tokens (see config.json), which would otherwise allow 32k.
    limited.llm_kwargs = copy.deepcopy(cfg.llm_kwargs or {})
    if "max_tokens" in limited.llm_kwargs.get("extra_body", {}):
        limited.llm_kwargs["extra_body"]["max_tokens"] = tokens
    return limited


class DetailedReport:
    def __init__(self, query: str, config_path: str, on_progress=print):
        self.query = query
        self.config_path = config_path
        self.on_progress = on_progress
        self.researcher = GPTResearcher(query=query, report_type="research_report", config_path=config_path)
        self.existing_headers: list[dict] = []
        self.written_sections: list[dict] = []
        self.references: list[str] = []

    async def run(self) -> str:
        self.on_progress(phases.BROAD_SEARCH)
        await self.researcher.conduct_research()
        subtopics = await self._get_subtopics()

        done = 0

        async def research(task: str) -> tuple[GPTResearcher, list[str]]:
            nonlocal done
            researched = await self._research(task)
            done += 1
            self.on_progress(phases.researched(done, len(subtopics)))
            return researched

        self.on_progress(phases.researched(0, len(subtopics)))
        introduction, *results = await asyncio.gather(
            self.researcher.write_introduction(), *(research(task) for task in subtopics), return_exceptions=True)
        if isinstance(introduction, BaseException):
            raise introduction
        failed = [(task, r) for task, r in zip(subtopics, results) if isinstance(r, BaseException)]
        for task, error in failed:
            logger.warning("Subtopic %r failed, its section is left out: %r", task, error)
        researched = [(task, r) for task, r in zip(subtopics, results) if not isinstance(r, BaseException)]
        if subtopics and not researched:
            raise failed[0][1]

        body = ""
        words = section_words(len(researched))
        for i, (task, (assistant, titles)) in enumerate(researched, 1):
            self.on_progress(phases.section(i, len(researched), task))
            assistant.cfg.total_words = words  # the section prompt's length (prompts.section_length)
            section = await self._write(task, assistant, titles)
            if section:
                body += f"\n\n\n{section}"

        self.on_progress(phases.CONCLUSION)
        toc = self.researcher.table_of_contents(body)
        conclusion = await self.researcher.write_report_conclusion(body)
        report = f"{introduction}\n\n{toc}\n\n{body}\n\n{conclusion}"

        # Upstream lists every visited URL, including pages dropped as irrelevant;
        # list what the text actually cites, unless it barely cites inline.
        cited = list(dict.fromkeys(m.group(2) for m in MARKDOWN_LINK.finditer(report)))
        self.references = cited if len(cited) >= 3 else sorted(self.researcher.visited_urls)
        heading = "Источники" if self.researcher.cfg.language.lower() == "russian" else "References"
        return report + f"\n\n\n## {heading}\n\n" + "".join(f"- [{url}]({url})\n" for url in self.references)

    def source_urls(self) -> list[str]:
        return self.references

    async def _get_subtopics(self) -> list[str]:
        r = self.researcher
        data = await construct_subtopics(task=r.query, data=r.context, config=_limited(r.cfg, SUBTOPICS_TOKENS),
                                         subtopics=r.subtopics, prompt_family=r.prompt_family)
        if not data or not getattr(data, "subtopics", None):
            return []
        return [subtopic.task for subtopic in data.subtopics]

    async def _research(self, task: str) -> tuple[GPTResearcher, list[str]]:
        """A subtopic's own research (it replaces whatever context the researcher had) and its draft titles."""
        assistant = GPTResearcher(
            query=task,
            report_type="subtopic_report",
            config_path=self.config_path,
            parent_query=self.query,
            visited_urls=self.researcher.visited_urls,  # one set for all: shared, not copied
            agent=self.researcher.agent,
            role=self.researcher.role,
        )
        await assistant.conduct_research()
        draft = await generate_draft_section_titles(
            query=assistant.query, current_subtopic=task, context=assistant.context,
            role=assistant.cfg.agent_role or assistant.role, config=_limited(assistant.cfg, TITLES_TOKENS, fast=True),
            cost_callback=assistant.add_costs, prompt_family=assistant.prompt_family,
        )
        titles = [h.get("text", "") for h in self.researcher.extract_headers("\n".join(draft or []))]
        return assistant, titles[:MAX_TITLES]

    async def _write(self, task: str, assistant: GPTResearcher, titles: list[str]) -> str:
        relevant = await assistant.get_similar_written_contents_by_draft_section_titles(task, titles, self.written_sections)
        section = await assistant.write_report(existing_headers=self.existing_headers, relevant_written_contents=relevant)
        self.written_sections.extend(self.researcher.extract_sections(section))
        self.existing_headers.append({"subtopic task": task, "headers": self.researcher.extract_headers(section)})
        return section
