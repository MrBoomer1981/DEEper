"""DEEper's prompts for gpt-researcher. Importing this module registers the "deeper" prompt family
(config.json PROMPT_FAMILY) and patches the spots where upstream does not pass the family on.

- Search queries are English-first: the report stays in LANGUAGE (Russian by default), but the queries
  the model generates are in English, where most research and industry sources are. The user's original
  query is still searched as typed (gpt-researcher appends it to the generated sub-queries), which keeps
  some sources in the user's language.
- The writer cites a source only for what that source says. Upstream asks for a citation on every
  substantive claim, at the end of the sentence or paragraph; the fact-check then found the report's own
  conclusions carrying a citation «for show» and facts that no page the research read states (plan 3.13).
- A report is at most REPORT_WORDS words. Upstream asks for at least TOTAL_WORDS per report, and per
  section in the detailed report, whose four sections then ran to 7-9 thousand words with Pro writing.
- The quick report is the overview, the default since 25.10.2026: about OVERVIEW_WORDS words to read in 5–7
  minutes, with «Главное» first and «Чего пока не знаем» last (overview_rules()). Upstream asked it for «a detailed
  report» of «at least TOTAL_WORDS words», «as long as you can»: 2–3.5 thousand words.
- A report that continues a line of research is told what the reader already read (`reader`, set per job by the
  worker's job loop): every writing prompt gets the block, so a continuation starts from what is new.
"""

import contextvars
import re
from datetime import datetime, timezone

from gpt_researcher import prompts
from gpt_researcher.actions import query_processing
from gpt_researcher.skills import writer
from gpt_researcher.skills.deep_research import DeepResearchSkill

from my_browser.common.report import OVERVIEW_HEADINGS, OVERVIEW_MAX, OVERVIEW_MIN, OVERVIEW_WORDS

QUERY_LANGUAGE_RULE = (
    "All queries must be in English, even though the task may be written in another language: "
    "English-language sources are the priority for this research. The language the task is written "
    "in is NOT a reason to use it. Only if the task is specifically about one country's laws, prices, "
    "institutions or local events, write exactly one of the queries in that country's language."
)

CITATION_RULES = """Citations. These rules take precedence over anything said about citations above:
1. Every fact, figure, date, name or quote you take from the information, and every forecast or opinion a source states, carries an in-text citation of the source it came from: the URL given with that information, and the same source's name in the citation text.
2. Cite a source only for what that source itself says. Your own analysis (comparisons, generalizations, implications, forecasts and recommendations you draw from the facts) goes WITHOUT a citation, worded so that the reader can tell it is the report's own reasoning. The facts it rests on are cited in their own sentences.
3. Keep a cited fact and your interpretation of it in separate sentences. Put each citation right after the sentence it supports, or right after its part when one sentence combines facts from several sources; never one citation at the end of a paragraph for the whole paragraph.
4. Specific facts (numbers, dates, names, company plans, results) come only from the information above, never from your own knowledge: if the information does not give a fact, leave it out.
5. Never add a citation just to make a statement look supported."""

# The conclusion is written from the report itself, not from the sources.
CONCLUSION_RULES = """Citations. These rules take precedence over anything said about citations above:
- When you restate a specific fact or figure from the report, keep the citation the report gives it (the same link), right after that sentence.
- Your conclusions, implications and next steps go without citations. Do not add facts or citations that are not in the report."""

# Length, the user's limit (20.10.2026): a whole report, its table of contents and references included,
# is at most REPORT_WORDS words. The detailed report gives its introduction and conclusion theirs, and its
# sections share the rest (detailed.section_words()).
REPORT_WORDS = 5000
INTRO_WORDS, CONCLUSION_WORDS = 300, 350
OVERSHOOT = 1.15  # how far above a section's length the writer may go
SECTION_SUBSECTIONS = 3  # ### parts of a detailed report's section; upstream allows 5, each a line of the contents

# Upstream's citation instructions (gpt-researcher 0.16.1) that the rules above replace. If an upgrade
# rewords them, the rules are still appended, and the old wording stays beside them.
EVERY_CLAIM = re.compile(r"^- Every substantive claim, figure or quote MUST carry an in-text citation[^\n]*\n", re.M)
PLACEMENT = re.compile(r"(?:you must )?use in-text citation references in (\S+) format and make it with markdown hyperlink "
                       r"placed at the end of the sentence or paragraph that references them like this: "
                       r"\(\[in-text citation\]\(url\)\)\.", re.I)
# A detailed report's section: «about N words» below replaces upstream's minimum of N words.
MINIMUM_LENGTH = re.compile(r"^- The report should have a minimum length of \d+ words\.\n", re.M)

# Upstream's research_report asks for «a detailed report» of «at least {total_words} words», «as long as you can»;
# the overview asks for a short one instead. If an upgrade rewords it, the overview's rules still take precedence.
LONG_ASK = re.compile(r" in a detailed report --\s*The report should focus on the answer to the query.*?"
                      r"necessary information provided\.\n", re.S)
SHORT_ASK = " in a short report that answers it directly, with the most important facts and numbers.\n"

LENGTH_HEADER = "Length. This rule takes precedence over anything said about length above:"

# What the reader already knows when a report continues a line of research: reader_text() of the job's branch and
# route, set by the worker's job loop for the run. Every writing prompt ends with it.
reader = contextvars.ContextVar("reader", default="")


def report_length(**_) -> str:
    return (f"{LENGTH_HEADER} the whole report, references included, is at most {REPORT_WORDS} words. "
            "When the information is richer than that, keep the most important facts and figures and leave the rest out.")


def section_length(total_words: int, **_) -> str:
    return (f"{LENGTH_HEADER} this section is about {total_words} words and never more than {round(total_words * OVERSHOOT)}. "
            f"It is one of several sections of a report of at most {REPORT_WORDS} words, so keep to the subtopic's "
            "most important facts and figures.")


def intro_length(**_) -> str:
    return f"{LENGTH_HEADER} the introduction is at most {INTRO_WORDS} words."


def conclusion_length(**_) -> str:
    return f"{LENGTH_HEADER} the conclusion is at most {CONCLUSION_WORDS} words."


def overview_rules(language: str = "russian", **_) -> str:
    summary, gaps = OVERVIEW_HEADINGS.get(str(language).lower(), OVERVIEW_HEADINGS["english"])
    return f"""Format. These rules take precedence over anything said about length and structure above:
- This is a briefing to read on a phone in 5-7 minutes: about {OVERVIEW_WORDS} words of text, at least {OVERVIEW_MIN} and never more than {OVERVIEW_MAX}; the list of references does not count. Use that room: explain the key facts with their context, figures and examples, and leave out minor details rather than squeeze everything in.
- Structure: the # title; then "## {summary}" with 3-4 bullet points of one or two sentences each, which answer the query with the key facts; then 3-4 sections (##) that explain and support them, in short paragraphs; then "## {gaps}" with 2-4 bullet points: open questions, missing or conflicting data, what would change the answer; then the references.
- No introduction that restates the query and no conclusion section: "{summary}" is the summary.
- A table only where it compares several items on the same points."""


def reader_text(branch: list[dict] | None, route: dict | None) -> str:
    """The prompt's block about the reader: the reports of the branch they already read (nearest first, with their
    key points) and the route this report is a step of ({"steps": [{"query"}...], "at": n})."""
    parts = []
    if branch:
        read = "\n".join(f"{i}. «{b['query']}»" + (f"\n{b['summary']}" if b.get("summary") else "") for i, b in enumerate(branch, 1))
        parts.append("The reader. This report continues a line of research, and the reader has already read these reports "
                     f"(the nearest first) with these key points:\n{read}\n"
                     "Do not retell what they cover: refer to it in a clause at most and spend the words on what is new to the "
                     "reader. Where the new information changes or contradicts an earlier report, say so.")
    if route and route.get("steps") and route.get("at"):
        steps = "\n".join(f"{n}. {s['query']}" + ("  <- this report" if n == route["at"] else "")
                          for n, s in enumerate(route["steps"], 1))
        parts.append(f"The route. This report is step {route['at']} of {len(route['steps'])} of a short course through the "
                     f"topic:\n{steps}\nAnswer this step's question; leave the other steps' questions to their reports.")
    return "\n\n".join(parts)


def _writing(upstream, length, rules: str = CITATION_RULES, rewrite=None, **defaults):
    """Upstream's writing prompt with our citation rules in place of its own, our length limit and, when the report
    continues a line of research, what the reader already knows."""
    def prompt(*args, **kwargs) -> str:
        kwargs = {**defaults, **kwargs}
        text = upstream(*args, **kwargs)
        if rewrite:
            text = rewrite(text)
        text = EVERY_CLAIM.sub("", text)
        text = PLACEMENT.sub(r"Use in-text citations in \1 format, as markdown hyperlinks like this: ([in-text citation](url)).", text)
        text = MINIMUM_LENGTH.sub("", text)
        known = reader.get()
        return f"{text.rstrip()}\n\n{rules}\n\n{length(**kwargs)}\n" + (f"\n{known}\n" if known else "")
    return staticmethod(prompt)


class DeeperPromptFamily(prompts.PromptFamily):
    @staticmethod
    def generate_search_queries_prompt(question, parent_query, report_type, max_iterations=3, context=[]):
        prompt = prompts.PromptFamily.generate_search_queries_prompt(
            question, parent_query, report_type, max_iterations, context
        )
        return prompt.replace("You must respond with a list of strings", f"{QUERY_LANGUAGE_RULE}\n\nYou must respond with a list of strings")

    # quick mode (the overview); detailed mode's sections, introduction and conclusion; deep mode
    generate_report_prompt = _writing(prompts.PromptFamily.generate_report_prompt, overview_rules,
                                      rewrite=lambda text: LONG_ASK.sub(SHORT_ASK, text, count=1))
    generate_subtopic_report_prompt = _writing(prompts.PromptFamily.generate_subtopic_report_prompt, section_length,
                                               max_subsections=SECTION_SUBSECTIONS)
    generate_report_introduction = _writing(prompts.PromptFamily.generate_report_introduction, intro_length)
    generate_report_conclusion = _writing(prompts.PromptFamily.generate_report_conclusion, conclusion_length, CONCLUSION_RULES)
    generate_deep_research_prompt = _writing(prompts.PromptFamily.generate_deep_research_prompt, report_length)


prompts.prompt_family_mapping["deeper"] = DeeperPromptFamily

_generate_sub_queries = query_processing.generate_sub_queries


async def _generate_sub_queries_with_family(query, parent_query, report_type, context, cfg, cost_callback=None, **kwargs):
    # Upstream plan_research_outline() doesn't pass prompt_family, so without this
    # PROMPT_FAMILY never applies to sub-query generation.
    kwargs.setdefault("prompt_family", prompts.get_prompt_family(cfg.prompt_family, cfg))
    return await _generate_sub_queries(query, parent_query, report_type, context, cfg, cost_callback, **kwargs)


query_processing.generate_sub_queries = _generate_sub_queries_with_family

_generate_report = writer.generate_report


async def _generate_report_with_family(*args, **kwargs):
    # Upstream ReportGenerator.write_report() doesn't pass prompt_family either, so without this the
    # report itself (quick, the detailed report's sections, deep) is written with upstream's prompts.
    if (cfg := kwargs.get("cfg")) is not None:
        kwargs.setdefault("prompt_family", prompts.get_prompt_family(cfg.prompt_family, cfg))
    return await _generate_report(*args, **kwargs)


writer.generate_report = _generate_report_with_family  # writer.py imported it by name

_deep_generate_search_queries = DeepResearchSkill.generate_search_queries


async def _english_deep_search_queries(self, query: str, num_queries: int = 3):
    # Upstream's inline prompt has no date, so the model tends to search for stale years.
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    return await _deep_generate_search_queries(
        self, f"{query}\n\n{QUERY_LANGUAGE_RULE}\nThe current date is {today}.", num_queries
    )


DeepResearchSkill.generate_search_queries = _english_deep_search_queries
