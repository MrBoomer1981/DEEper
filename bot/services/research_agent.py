"""
Core research agent — orchestrates the full research pipeline.
All reports are generated in Russian.
Uses GroqKeyRotator for automatic key rotation on rate limits.
"""
import asyncio
from typing import Callable, List, Optional

from bot.config import Config, ResearchMode, RESEARCH_MODES
from bot.services.cache_manager import CacheManager
from bot.services.groq_rotator import GroqKeyRotator
from bot.services.knowledge_base import KnowledgeBase
from bot.services.planner import ResearchPlanner
from bot.services.web_scraper import WebScraper
from bot.services.web_search import WebSearch
from bot.utils.logger import get_logger
from bot.utils.text_utils import chunk_text, truncate_text

logger = get_logger("research_agent")

CHUNK_ANALYSIS_SYSTEM = """Ты — аналитик-исследователь. Извлеки ключевые факты и данные 
из предоставленного текста веб-страницы. Отвечай ТОЛЬКО на русском языке. 
Будь конкретным, фокусируйся на фактах, цифрах и важных выводах."""

REPORT_SYSTEM = """Ты — эксперт-аналитик. Создай подробный структурированный отчёт на РУССКОМ языке.

Структура отчёта (строго соблюдай):

# {title}

## Краткое резюме
[2-3 предложения — главный вывод]

## Ключевые находки
[5-8 пунктов — самые важные открытия]

## Риски и ограничения
[Потенциальные риски, ограничения, оговорки]

## Научные и технические подробности
[Глубокий технический/научный анализ]

## Заключение
[Итоговый вывод и рекомендации]

## Источники
{sources_placeholder}

Отчёт должен быть объективным, основанным на фактах, написанным по-русски."""


class ResearchAgent:
    def __init__(self, config: Config, knowledge_base: KnowledgeBase) -> None:
        self.config = config
        self.kb = knowledge_base
        self.rotator = GroqKeyRotator()
        self.cache = CacheManager(db_path=config.db_path)
        self.scraper = WebScraper(
            cache=self.cache,
            timeout=config.scrape_timeout,
            retries=config.scrape_retries,
        )

    async def research(
        self,
        topic: str,
        mode_name: str = "deep",
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        mode: ResearchMode = RESEARCH_MODES.get(mode_name, RESEARCH_MODES["deep"])

        async def notify(msg: str) -> None:
            if progress_callback:
                try:
                    await progress_callback(msg)
                except Exception:
                    pass

        planner = ResearchPlanner(
            groq_api_key=self.config.groq_api_key,
            primary_model=self.config.primary_model,
            n_queries=mode.search_queries,
        )
        searcher = WebSearch(
            api_key=self.config.tavily_api_key,
            pages_per_query=self.config.pages_per_query,
        )

        await notify(f"🧠 Составляю план исследования ({mode.label})...")
        plan = await planner.generate_plan(topic)
        title = plan.get("title", topic)
        queries = plan.get("queries", [topic])
        logger.info("Режим: {} | Тема: '{}' | Запросов: {}", mode.name, title, len(queries))

        await notify(f"🔍 Выполняю {len(queries)} поисковых запросов...")
        search_results = await searcher.search_many(queries)

        all_urls: List[str] = []
        all_snippets: List[str] = []
        seen: set = set()
        for query, result in search_results.items():
            for url in result["urls"]:
                if url not in seen:
                    seen.add(url)
                    all_urls.append(url)
            all_snippets.extend(result["snippets"])

        all_urls = all_urls[:mode.max_pages]
        await notify(f"📄 Анализирую {len(all_urls)} страниц...")

        page_texts = await self.scraper.scrape_many(all_urls)

        await notify(f"🔬 Извлекаю данные из {len(page_texts)} страниц...")
        findings = await self._analyze_pages(page_texts, topic, mode.max_chunks_per_page)

        await notify("✍️ Формирую отчёт на русском языке...")
        report = await self._generate_report(topic, title, plan, findings, all_snippets, all_urls)
        summary = await self._generate_summary(topic, report)

        await notify("💾 Сохраняю в базу знаний...")
        research_id = await self.kb.save_research(
            title=title,
            summary=summary,
            report=report,
            sources=all_urls,
        )

        logger.info("Исследование #{} завершено: '{}'", research_id, title)
        return {
            "research_id": research_id,
            "title": title,
            "report": report,
            "sources": all_urls,
            "summary": summary,
            "mode": mode.label,
        }

    async def _analyze_pages(self, page_texts: List[str], topic: str, max_chunks: int) -> List[str]:
        all_findings: List[str] = []
        # Concurrency=3 — one per key, rotator handles 429s automatically
        semaphore = asyncio.Semaphore(3)

        async def analyze_page(text: str) -> List[str]:
            chunks = chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
            findings = []
            async with semaphore:
                for chunk in chunks[:max_chunks]:
                    f = await self._analyze_chunk(chunk, topic)
                    if f:
                        findings.append(f)
            return findings

        results = await asyncio.gather(
            *[analyze_page(t) for t in page_texts],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                all_findings.extend(r)

        logger.info("Извлечено {} фактов", len(all_findings))
        return all_findings

    async def _analyze_chunk(self, chunk: str, topic: str) -> Optional[str]:
        try:
            return await self.rotator.chat(
                model=self.config.fast_model,
                messages=[
                    {"role": "system", "content": CHUNK_ANALYSIS_SYSTEM},
                    {"role": "user", "content": f"Тема: {topic}\n\nТекст:\n{chunk}"},
                ],
                max_tokens=400,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("Ошибка анализа чанка: {}", e)
            return None

    async def _generate_report(self, topic, title, plan, findings, snippets, sources) -> str:
        findings_text = truncate_text("\n\n".join(findings), max_tokens=5000)
        sources_fmt = "\n".join(f"{i+1}. {u}" for i, u in enumerate(sources))
        snippet_ctx = truncate_text("\n".join(snippets[:30]), 1000)

        prompt = f"""Тема исследования: {topic}

Обзор плана: {plan.get("overview", "")}
Углы исследования: {", ".join(plan.get("angles", []))}

Извлечённые факты ({len(findings)} источников):
{findings_text}

Дополнительный контекст:
{snippet_ctx}

Напиши полный отчёт на русском языке."""

        try:
            return await self.rotator.chat(
                model=self.config.primary_model,
                messages=[
                    {"role": "system", "content": REPORT_SYSTEM.format(
                        title=title, sources_placeholder=sources_fmt)},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.4,
            )
        except Exception as e:
            logger.error("Ошибка генерации отчёта: {}", e)
            return (
                f"# {title}\n\n## Краткое резюме\nИсследование по теме: {topic}\n\n"
                f"## Ключевые находки\n" + "\n".join(f"- {f[:200]}" for f in findings[:10])
                + f"\n\n## Источники\n{sources_fmt}"
            )

    async def _generate_summary(self, topic: str, report: str) -> str:
        try:
            return await self.rotator.chat(
                model=self.config.fast_model,
                messages=[
                    {"role": "system", "content": "Создай краткое резюме из 2-3 предложений на русском языке."},
                    {"role": "user", "content": truncate_text(report, 3000)},
                ],
                max_tokens=300,
                temperature=0.3,
            )
        except Exception:
            return f"Исследование по теме: {topic}"
