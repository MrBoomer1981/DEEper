"""
Research planner — generates a structured plan and search queries.
All output is always in Russian.
"""
import json
from typing import List

from bot.services.groq_rotator import GroqKeyRotator
from bot.utils.logger import get_logger
from bot.utils.text_utils import truncate_text

logger = get_logger("planner")

PLAN_SYSTEM = """Ты — опытный исследователь и аналитик. Всегда отвечай ТОЛЬКО на русском языке.

Получив тему для исследования, создай структурированный JSON-план:
- "title": краткое название исследования на русском
- "overview": 2-3 предложения о том, что будет исследоваться
- "angles": список из 5-7 различных углов исследования
- "queries": список из ровно {n_queries} конкретных поисковых запросов на русском и английском языках

Отвечай ТОЛЬКО валидным JSON. Без markdown, без пояснений."""

PLAN_USER = """Тема исследования: {topic}

Создай план с {n_queries} поисковыми запросами, охватывающими разные аспекты:
технические детали, последние разработки, мнения экспертов, статистику и практическое применение.
Запросы должны быть на русском И английском языках для максимального охвата."""


class ResearchPlanner:
    def __init__(self, groq_api_key: str, primary_model: str, n_queries: int = 15) -> None:
        # groq_api_key kept for interface compatibility
        self.rotator = GroqKeyRotator()
        self.primary_model = primary_model
        self.n_queries = n_queries

    async def generate_plan(self, topic: str) -> dict:
        system = PLAN_SYSTEM.format(n_queries=self.n_queries)
        user = PLAN_USER.format(topic=topic, n_queries=self.n_queries)

        try:
            raw = await self.rotator.chat(
                model=self.primary_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=2048,
                temperature=0.7,
            )
        except Exception as e:
            logger.warning("Ошибка генерации плана: {}. Используем fallback.", e)
            return self._fallback_plan(topic)

        try:
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            plan = json.loads(clean)
            queries = plan.get("queries", [])
            if len(queries) < self.n_queries:
                queries += [f"{topic} {a}" for a in plan.get("angles", [])]
            plan["queries"] = queries[:self.n_queries]
            logger.info("План создан: '{}' ({} запросов)", plan.get("title", topic), len(plan["queries"]))
            return plan
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Ошибка парсинга плана: {}. Используем fallback.", e)
            return self._fallback_plan(topic)

    def _fallback_plan(self, topic: str) -> dict:
        queries = [
            f"{topic} обзор",
            f"{topic} последние исследования",
            f"{topic} технические детали",
            f"{topic} применение на практике",
            f"{topic} статистика 2024",
            f"{topic} мнение экспертов",
            f"{topic} риски и проблемы",
            f"{topic} future trends",
            f"{topic} overview",
            f"{topic} research",
            f"{topic} технологии",
            f"как работает {topic}",
            f"{topic} примеры",
            f"{topic} научные исследования",
            f"{topic} best practices",
            f"{topic} история развития",
            f"{topic} сравнение методов",
            f"{topic} инновации",
            f"{topic} applications",
            f"{topic} анализ",
        ]
        return {
            "title": topic,
            "overview": f"Комплексное исследование по теме: {topic}",
            "angles": ["обзор", "технологии", "применение", "риски", "тенденции"],
            "queries": queries[:self.n_queries],
        }
