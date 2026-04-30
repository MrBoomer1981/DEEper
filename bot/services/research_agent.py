"""
Core research agent — orchestrates the full research pipeline:
plan → search → scrape → chunk → analyze → report → save.
"""
import asyncio
from typing import AsyncIterator, Callable, List, Optional

from groq import Groq

from bot.config import Config
from bot.services.cache_manager import CacheManager
from bot.services.knowledge_base import KnowledgeBase
from bot.services.planner import ResearchPlanner
from bot.services.web_scraper import WebScraper
from bot.services.web_search import WebSearch
from bot.utils.logger import get_logger
from bot.utils.text_utils import chunk_text, truncate_text

logger = get_logger("research_agent")

# ── Prompt templates ──────────────────────────────────────────────────────────

CHUNK_ANALYSIS_SYSTEM = """You are a research analyst. Extract key factual insights 
from the provided web page content. Be concise and specific. Focus on data, 
facts, and concrete information relevant to the research topic."""

REPORT_SYSTEM = """You are an expert research writer. Create a comprehensive, 
well-structured research report based on the provided research findings.

The report MUST be written in the SAME LANGUAGE as the user's original query.

Structure the report exactly as follows:

# {title}

## Summary
[2-3 sentence executive summary]

## Key Findings
[5-8 bullet points of the most important discoveries]

## Risks and Considerations
[Potential risks, limitations, or caveats]

## Scientific and Technical Insights
[Deep-dive into technical/scientific aspects]

## Conclusion
[Final synthesis and takeaway]

## Sources
{sources_placeholder}

Be thorough, objective, and evidence-based."""


class ResearchAgent:
    """Orchestrates the full deep research pipeline."""

    def __init__(self, config: Config, knowledge_base: KnowledgeBase) -> None:
        self.config = config
        self.kb = knowledge_base

        self.client = Groq(api_key=config.groq_api_key)
        self.planner = ResearchPlanner(
            groq_api_key=config.groq_api_key,
            primary_model=config.primary_model,
            n_queries=config.search_queries_count,
        )
        self.searcher = WebSearch(
            api_key=config.tavily_api_key,
            pages_per_query=config.pages_per_query,
        )
        self.cache = CacheManager(db_path=config.db_path)
        self.scraper = WebScraper(
            cache=self.cache,
            timeout=config.scrape_timeout,
            retries=config.scrape_retries,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def research(
        self,
        topic: str,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Run the full research pipeline for a given topic.

        Args:
            topic: User's research query.
            progress_callback: Async callable that receives progress status strings.

        Returns:
            dict with keys: research_id, title, report, sources, summary.
        """

        async def notify(msg: str) -> None:
            if progress_callback:
                try:
                    await progress_callback(msg)
                except Exception:
                    pass

        # Step 1: Generate research plan
        await notify("🧠 Generating research plan...")
        plan = await self.planner.generate_plan(topic)
        title = plan.get("title", topic)
        queries = plan.get("queries", [topic])
        logger.info("Research started: '{}' | {} queries", title, len(queries))

        # Step 2: Search
        await notify(f"🔍 Running {len(queries)} search queries...")
        search_results = await self.searcher.search_many(queries)

        # Step 3: Collect unique URLs (respect max_pages limit)
        all_urls: List[str] = []
        all_snippets: List[str] = []
        seen: set = set()
        for query, result in search_results.items():
            for url in result["urls"]:
                if url not in seen:
                    seen.add(url)
                    all_urls.append(url)
            all_snippets.extend(result["snippets"])

        all_urls = all_urls[: self.config.max_pages]
        logger.info("Collected {} unique URLs to scrape", len(all_urls))
        await notify(f"📄 Scraping {len(all_urls)} web pages...")

        # Step 4: Scrape pages
        page_texts = await self.scraper.scrape_many(all_urls)

        # Step 5: Chunk + analyze
        await notify(f"🔬 Analyzing content from {len(page_texts)} pages...")
        findings = await self._analyze_pages(page_texts, topic)

        # Step 6: Build snippets context
        snippet_context = "\n".join(all_snippets[:30])  # limit context size

        # Step 7: Generate final report
        await notify("✍️ Writing research report...")
        report = await self._generate_report(
            topic=topic,
            title=title,
            plan=plan,
            findings=findings,
            snippet_context=snippet_context,
            sources=all_urls,
        )

        # Step 8: Generate short summary
        summary = await self._generate_summary(topic, report)

        # Step 9: Save to knowledge base
        await notify("💾 Saving to knowledge base...")
        research_id = await self.kb.save_research(
            title=title,
            summary=summary,
            report=report,
            sources=all_urls,
        )

        logger.info("Research #{} completed: '{}'", research_id, title)
        return {
            "research_id": research_id,
            "title": title,
            "report": report,
            "sources": all_urls,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    async def _analyze_pages(self, page_texts: List[str], topic: str) -> List[str]:
        """Analyze each page's chunks and extract key findings."""
        all_findings: List[str] = []
        semaphore = asyncio.Semaphore(4)

        async def analyze_page(text: str, page_idx: int) -> List[str]:
            chunks = chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
            page_findings: List[str] = []

            async with semaphore:
                for chunk in chunks[:5]:  # max 5 chunks per page
                    finding = await self._analyze_chunk(chunk, topic)
                    if finding:
                        page_findings.append(finding)
            return page_findings

        tasks = [analyze_page(t, i) for i, t in enumerate(page_texts)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, list):
                all_findings.extend(r)
            elif isinstance(r, Exception):
                logger.warning("Page analysis error: {}", r)

        logger.info("Extracted {} findings from pages", len(all_findings))
        return all_findings

    async def _analyze_chunk(self, chunk: str, topic: str) -> Optional[str]:
        """Ask LLM to extract key insights from a single chunk."""
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.config.fast_model,
                    messages=[
                        {"role": "system", "content": CHUNK_ANALYSIS_SYSTEM},
                        {
                            "role": "user",
                            "content": f"Research topic: {topic}\n\nContent:\n{chunk}",
                        },
                    ],
                    max_tokens=400,
                    temperature=0.2,
                ),
            )
            return result.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Chunk analysis failed: {}", e)
            return None

    async def _generate_report(
        self,
        topic: str,
        title: str,
        plan: dict,
        findings: List[str],
        snippet_context: str,
        sources: List[str],
    ) -> str:
        """Synthesize all findings into a final structured report."""
        # Build aggregated findings text (respect token limits)
        findings_text = "\n\n".join(findings)
        findings_text = truncate_text(findings_text, max_tokens=5000)

        sources_formatted = "\n".join(
            f"{i + 1}. {url}" for i, url in enumerate(sources)
        )

        prompt = f"""Research topic: {topic}

Research plan overview: {plan.get("overview", "")}

Key angles researched: {", ".join(plan.get("angles", []))}

Extracted findings from {len(findings)} analysis results:
{findings_text}

Additional context from search snippets:
{truncate_text(snippet_context, 1000)}

Write a comprehensive research report covering all angles."""

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.config.primary_model,
                    messages=[
                        {
                            "role": "system",
                            "content": REPORT_SYSTEM.format(
                                title=title,
                                sources_placeholder=sources_formatted,
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4000,
                    temperature=0.4,
                ),
            )
            report = response.choices[0].message.content.strip()

            # Append sources if not already present
            if "## Sources" not in report and sources:
                report += f"\n\n## Sources\n{sources_formatted}"

            return report

        except Exception as e:
            logger.error("Report generation failed: {}", e)
            return (
                f"# {title}\n\n"
                f"## Summary\nResearch completed on: {topic}\n\n"
                f"## Findings\n" + "\n".join(f"- {f[:200]}" for f in findings[:10])
                + f"\n\n## Sources\n{sources_formatted}"
            )

    async def _generate_summary(self, topic: str, report: str) -> str:
        """Generate a short 2-3 sentence summary of the report."""
        loop = asyncio.get_event_loop()
        try:
            truncated_report = truncate_text(report, max_tokens=3000)
            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.config.fast_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Create a concise 2-3 sentence summary of the research report. "
                            "Write in the SAME language as the report.",
                        },
                        {"role": "user", "content": truncated_report},
                    ],
                    max_tokens=300,
                    temperature=0.3,
                ),
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Summary generation failed: {}", e)
            return f"Research report on: {topic}"
