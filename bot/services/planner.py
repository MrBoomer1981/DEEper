"""
Research planner — uses LLM to generate a structured research plan
and a list of diverse search queries for a given topic.
"""
import json
from typing import List

from groq import Groq

from bot.utils.logger import get_logger
from bot.utils.text_utils import truncate_text

logger = get_logger("planner")

PLAN_SYSTEM = """You are an expert research planner. 
Given a research topic, produce a structured JSON plan with:
- "title": concise research title
- "overview": 2-3 sentence description of what will be researched
- "angles": list of 5-7 distinct research angles to explore
- "queries": list of exactly {n_queries} specific, diverse web search queries

Respond ONLY with valid JSON. No markdown, no explanation."""

PLAN_USER = """Research topic: {topic}

Generate a comprehensive research plan with {n_queries} search queries 
covering different aspects: technical details, recent developments, 
expert opinions, statistics, and practical applications."""


class ResearchPlanner:
    def __init__(self, groq_api_key: str, primary_model: str, n_queries: int = 15) -> None:
        self.client = Groq(api_key=groq_api_key)
        self.primary_model = primary_model
        self.n_queries = n_queries

    def _call_llm(self, system: str, user: str, model: str, max_tokens: int = 2048) -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()

    async def generate_plan(self, topic: str) -> dict:
        """
        Generate a research plan for the given topic.

        Returns a dict with keys: title, overview, angles, queries.
        Falls back to a simple plan if JSON parsing fails.
        """
        import asyncio

        system = PLAN_SYSTEM.format(n_queries=self.n_queries)
        user = PLAN_USER.format(topic=topic, n_queries=self.n_queries)

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: self._call_llm(system, user, self.primary_model),
        )

        try:
            # Strip potential markdown fences
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            plan = json.loads(clean)
            # Ensure we have exactly n_queries
            queries = plan.get("queries", [])
            if len(queries) < self.n_queries:
                queries += [f"{topic} {angle}" for angle in plan.get("angles", [])]
                queries = queries[: self.n_queries]
            plan["queries"] = queries[: self.n_queries]
            logger.info("Plan generated: '{}' with {} queries", plan.get("title", topic), len(queries))
            return plan

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse plan JSON: {}. Using fallback.", e)
            return self._fallback_plan(topic)

    def _fallback_plan(self, topic: str) -> dict:
        queries = [
            f"{topic} overview",
            f"{topic} latest research",
            f"{topic} technical details",
            f"{topic} case studies",
            f"{topic} statistics 2024",
            f"{topic} expert opinion",
            f"{topic} challenges",
            f"{topic} future trends",
            f"{topic} applications",
            f"{topic} comparison",
            f"{topic} benefits risks",
            f"how does {topic} work",
            f"{topic} industry impact",
            f"{topic} scientific studies",
            f"{topic} best practices",
        ]
        return {
            "title": topic,
            "overview": f"Comprehensive research on the topic: {topic}",
            "angles": ["overview", "technical", "applications", "risks", "trends"],
            "queries": queries[: self.n_queries],
        }
