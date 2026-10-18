"""Detailed (multi-section) report.

Port of GPT Researcher's backend DetailedReport, which is not shipped in the
pip package: one broad research pass, then the topic is split into subtopics,
each researched and written as its own section, then stitched together with an
introduction, table of contents, conclusion and references.
"""

from gpt_researcher import GPTResearcher


class DetailedReport:
    def __init__(self, query: str, config_path: str, on_progress=print):
        self.query = query
        self.config_path = config_path
        self.on_progress = on_progress
        self.researcher = GPTResearcher(query=query, report_type="research_report", config_path=config_path)
        self.existing_headers: list[dict] = []
        self.global_context: list[str] = []
        self.global_written_sections: list[dict] = []
        self.global_urls: set[str] = set()

    async def run(self) -> str:
        self.on_progress("Общий поиск по теме...")
        await self.researcher.conduct_research()
        self.global_context = self.researcher.context
        self.global_urls = self.researcher.visited_urls

        subtopics = await self._get_subtopics()
        self.on_progress(f"Разделов: {len(subtopics)}. Пишу введение...")
        introduction = await self.researcher.write_introduction()

        body = ""
        for i, task in enumerate(subtopics, 1):
            self.on_progress(f"Раздел {i}/{len(subtopics)}: {task}")
            section = await self._write_subtopic(task)
            if section:
                body += f"\n\n\n{section}"

        self.researcher.visited_urls.update(self.global_urls)
        self.on_progress("Пишу заключение...")
        toc = self.researcher.table_of_contents(body)
        conclusion = await self.researcher.write_report_conclusion(body)
        conclusion = self.researcher.add_references(conclusion, self.researcher.visited_urls)
        return f"{introduction}\n\n{toc}\n\n{body}\n\n{conclusion}"

    def source_urls(self) -> list[str]:
        return sorted(self.researcher.visited_urls)

    async def _get_subtopics(self) -> list[str]:
        data = await self.researcher.get_subtopics()
        if not data or not getattr(data, "subtopics", None):
            return []
        return [subtopic.task for subtopic in data.subtopics]

    async def _write_subtopic(self, task: str) -> str:
        assistant = GPTResearcher(
            query=task,
            report_type="subtopic_report",
            config_path=self.config_path,
            parent_query=self.query,
            visited_urls=self.global_urls,
            agent=self.researcher.agent,
            role=self.researcher.role,
        )
        assistant.context = list(set(_as_strings(self.global_context)))
        await assistant.conduct_research()

        draft_titles = await assistant.get_draft_section_titles(task)
        titles = [h.get("text", "") for h in self.researcher.extract_headers(str(draft_titles))]
        relevant = await assistant.get_similar_written_contents_by_draft_section_titles(
            task, titles, self.global_written_sections
        )
        section = await assistant.write_report(
            existing_headers=self.existing_headers,
            relevant_written_contents=relevant,
        )

        self.global_written_sections.extend(self.researcher.extract_sections(section))
        self.global_context = list(set(_as_strings(assistant.context)))
        self.global_urls.update(assistant.visited_urls)
        self.existing_headers.append({"subtopic task": task, "headers": self.researcher.extract_headers(section)})
        return section


def _as_strings(context: list) -> list[str]:
    """Context items can be dicts (e.g. from MCP); make them hashable strings."""
    items = []
    for item in context:
        if isinstance(item, dict):
            items.append(f"Title: {item.get('title', 'No title')}\nContent: {item.get('body', item.get('content', ''))}")
        else:
            items.append(str(item))
    return items
