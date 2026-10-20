"""DEEper's prompts for gpt-researcher. Importing this module registers the "deeper" prompt family
(config.json PROMPT_FAMILY) and patches the spots where upstream does not pass the family on.

- Search queries are English-first: the report stays in LANGUAGE (Russian by default), but the queries
  the model generates are in English, where most research and industry sources are. The user's original
  query is still searched as typed (gpt-researcher appends it to the generated sub-queries), which keeps
  some sources in the user's language.
- The writer cites a source only for what that source says. Upstream asks for a citation on every
  substantive claim, at the end of the sentence or paragraph; the fact-check then found the report's own
  conclusions carrying a citation «for show» and facts that no page the research read states (plan 3.13).
"""

import re
from datetime import datetime, timezone

from gpt_researcher import prompts
from gpt_researcher.actions import query_processing
from gpt_researcher.skills import writer
from gpt_researcher.skills.deep_research import DeepResearchSkill

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

# Upstream's citation instructions (gpt-researcher 0.16.1) that the rules above replace. If an upgrade
# rewords them, the rules are still appended, and the old wording stays beside them.
EVERY_CLAIM = re.compile(r"^- Every substantive claim, figure or quote MUST carry an in-text citation[^\n]*\n", re.M)
PLACEMENT = re.compile(r"(?:you must )?use in-text citation references in (\S+) format and make it with markdown hyperlink "
                       r"placed at the end of the sentence or paragraph that references them like this: "
                       r"\(\[in-text citation\]\(url\)\)\.", re.I)


def _citing(upstream, rules: str = CITATION_RULES):
    """Upstream's writing prompt with our citation rules in place of its own."""
    def prompt(*args, **kwargs) -> str:
        text = EVERY_CLAIM.sub("", upstream(*args, **kwargs))
        text = PLACEMENT.sub(r"Use in-text citations in \1 format, as markdown hyperlinks like this: ([in-text citation](url)).", text)
        return f"{text.rstrip()}\n\n{rules}\n"
    return staticmethod(prompt)


class DeeperPromptFamily(prompts.PromptFamily):
    @staticmethod
    def generate_search_queries_prompt(question, parent_query, report_type, max_iterations=3, context=[]):
        prompt = prompts.PromptFamily.generate_search_queries_prompt(
            question, parent_query, report_type, max_iterations, context
        )
        return prompt.replace("You must respond with a list of strings", f"{QUERY_LANGUAGE_RULE}\n\nYou must respond with a list of strings")

    # quick mode; detailed mode's sections, introduction and conclusion; deep mode
    generate_report_prompt = _citing(prompts.PromptFamily.generate_report_prompt)
    generate_subtopic_report_prompt = _citing(prompts.PromptFamily.generate_subtopic_report_prompt)
    generate_report_introduction = _citing(prompts.PromptFamily.generate_report_introduction)
    generate_report_conclusion = _citing(prompts.PromptFamily.generate_report_conclusion, CONCLUSION_RULES)
    generate_deep_research_prompt = _citing(prompts.PromptFamily.generate_deep_research_prompt)


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
