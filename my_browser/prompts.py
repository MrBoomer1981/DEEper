"""English-first search queries: the report stays in LANGUAGE (Russian by default), but the
queries the model generates are in English, where most research and industry sources are.

Importing this module registers the "english_first" prompt family (config.json PROMPT_FAMILY)
and patches deep research, which builds its query prompt inline instead of via a prompt family.
The user's original query is still searched as typed (gpt-researcher appends it to the
generated sub-queries), which keeps some sources in the user's language.
"""

from datetime import datetime, timezone

from gpt_researcher import prompts
from gpt_researcher.actions import query_processing
from gpt_researcher.skills.deep_research import DeepResearchSkill

QUERY_LANGUAGE_RULE = (
    "All queries must be in English, even though the task may be written in another language: "
    "English-language sources are the priority for this research. The language the task is written "
    "in is NOT a reason to use it. Only if the task is specifically about one country's laws, prices, "
    "institutions or local events, write exactly one of the queries in that country's language."
)


class EnglishFirstPromptFamily(prompts.PromptFamily):
    @staticmethod
    def generate_search_queries_prompt(question, parent_query, report_type, max_iterations=3, context=[]):
        prompt = prompts.PromptFamily.generate_search_queries_prompt(
            question, parent_query, report_type, max_iterations, context
        )
        return prompt.replace("You must respond with a list of strings", f"{QUERY_LANGUAGE_RULE}\n\nYou must respond with a list of strings")


prompts.prompt_family_mapping["english_first"] = EnglishFirstPromptFamily

_generate_sub_queries = query_processing.generate_sub_queries


async def _generate_sub_queries_with_family(query, parent_query, report_type, context, cfg, cost_callback=None, **kwargs):
    # Upstream plan_research_outline() doesn't pass prompt_family, so without this
    # PROMPT_FAMILY never applies to sub-query generation.
    kwargs.setdefault("prompt_family", prompts.get_prompt_family(cfg.prompt_family, cfg))
    return await _generate_sub_queries(query, parent_query, report_type, context, cfg, cost_callback, **kwargs)


query_processing.generate_sub_queries = _generate_sub_queries_with_family

_deep_generate_search_queries = DeepResearchSkill.generate_search_queries


async def _english_deep_search_queries(self, query: str, num_queries: int = 3):
    # Upstream's inline prompt has no date, so the model tends to search for stale years.
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    return await _deep_generate_search_queries(
        self, f"{query}\n\n{QUERY_LANGUAGE_RULE}\nThe current date is {today}.", num_queries
    )


DeepResearchSkill.generate_search_queries = _english_deep_search_queries
