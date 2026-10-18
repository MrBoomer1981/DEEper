"""Real cost of LLM calls, from the token usage DeepSeek reports.

Importing this module wraps gpt-researcher's GenericLLMProvider.get_chat_response, so every call
adds its tokens to the Meter active in the current context (a ContextVar: set by a research run
in its worker thread, or around a single Q&A call). Streamed calls report usage only with
"stream_usage" in config.json's LLM_KWARGS; calls that bypass get_chat_response (gpt-researcher's
subtopic planning chain) are not counted, so totals are a slight underestimate.
"""

import contextvars
import json
import logging
import os
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from gpt_researcher.llm_provider.generic.base import GenericLLMProvider

logger = logging.getLogger(__name__)

# USD per 1M tokens at peak hours: (input cache miss, input cache hit, output); off-peak is half.
# https://api-docs.deepseek.com/quick_start/pricing (October 2026)
PRICES = {
    "deepseek-flash": (0.30, 0.006, 1.20),
    "deepseek-v4-pro": (1.32, 0.044, 3.96),
}
FALLBACK_USD_RUB = 90.0

current: contextvars.ContextVar["Meter | None"] = contextvars.ContextVar("cost_meter", default=None)
_rate = {"value": None, "at": 0.0}


def is_peak(moment: datetime) -> bool:
    """DeepSeek peak hours: 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday."""
    return moment.weekday() < 5 and (1 <= moment.hour < 4 or 6 <= moment.hour < 10)


def usd_rub(fetch: bool = True) -> float:
    """Central Bank of Russia rate, cached for 12 h; USD_RUB env var overrides it."""
    if os.environ.get("USD_RUB"):
        return float(os.environ["USD_RUB"])
    fresh = _rate["value"] and time.time() - _rate["at"] < 12 * 3600
    if fetch and not fresh:
        try:
            with urllib.request.urlopen("https://www.cbr-xml-daily.ru/daily_json.js", timeout=5) as r:
                _rate["value"] = float(json.load(r)["Valute"]["USD"]["Value"])
        except Exception as e:
            logger.warning("USD/RUB rate unavailable (%s), using %s", e, _rate["value"] or FALLBACK_USD_RUB)
        _rate["at"] = time.time()
    return _rate["value"] or FALLBACK_USD_RUB


@dataclass
class Meter:
    usd: float = 0.0
    unpriced_calls: int = 0
    truncated_calls: int = 0  # answers cut off by the output token limit
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, input_tokens: int, cached: int, output_tokens: int) -> None:
        row = self.by_model.setdefault(model, {"calls": 0, "input": 0, "cached": 0, "output": 0, "usd": 0.0})
        row["calls"] += 1
        row["input"] += input_tokens
        row["cached"] += cached
        row["output"] += output_tokens
        price = PRICES.get(model)
        if price is None:
            self.unpriced_calls += 1
            return
        factor = 1.0 if is_peak(datetime.now(timezone.utc)) else 0.5
        usd = factor * ((input_tokens - cached) * price[0] + cached * price[1] + output_tokens * price[2]) / 1e6
        row["usd"] += usd
        self.usd += usd

    def rub(self, fetch: bool = False) -> float:
        return self.usd * usd_rub(fetch)

    def summary(self, fetch: bool = True) -> dict:
        rate = usd_rub(fetch)
        return {"usd": round(self.usd, 5), "rub": round(self.usd * rate, 2), "usd_rub": rate,
                "unpriced_calls": self.unpriced_calls, "truncated_calls": self.truncated_calls, "by_model": self.by_model}


@contextmanager
def metered():
    """A fresh Meter for LLM calls outside a research run: a question, a clarification, follow-ups."""
    meter = Meter()
    token = current.set(meter)
    try:
        yield meter
    finally:
        current.reset(token)


def rub_text(rub: float | None) -> str:
    if rub is None:
        return "—"
    return f"{rub:.2f} ₽".replace(".", ",") if rub < 10 else f"{rub:.1f} ₽".replace(".", ",")


_get_chat_response = GenericLLMProvider.get_chat_response


async def _metered_get_chat_response(self, messages, stream, websocket=None, **kwargs):
    response = await _get_chat_response(self, messages, stream, websocket, **kwargs)
    meter, usage = current.get(), self.last_usage_metadata
    if (self.last_response_metadata or {}).get("finish_reason") == "length":
        logger.warning("LLM answer cut off by the output token limit (%s chars)", len(response or ""))
        if meter is not None:
            meter.truncated_calls += 1
    if meter is not None and usage:
        cached = (usage.get("input_token_details") or {}).get("cache_read") or 0
        model = getattr(self.llm, "model_name", None) or getattr(self.llm, "model", "?")
        meter.add(model, usage.get("input_tokens", 0), cached, usage.get("output_tokens", 0))
    return response


GenericLLMProvider.get_chat_response = _metered_get_chat_response
