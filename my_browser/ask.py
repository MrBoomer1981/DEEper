"""Follow-up questions about a saved report, answered from its text only.

The questions and answers are kept next to the report as <name>.qa.json, so the
Telegram bot and the web app share them. When the report has no answer, the model
names a topic for a separate research, which both UIs offer to run.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from my_browser.pipeline import CONFIG_PATH

PROMPT = """Ниже полный текст исследовательского отчёта. Ответь на вопрос пользователя, опираясь только на этот отчёт.
Отвечай по-русски, по существу, без вступлений. Сохраняй ссылки на источники из отчёта, если они относятся к ответу.
Если в отчёте нет ответа или он неполный, прямо скажи об этом и последней строкой напиши «ИССЛЕДОВАТЬ: тема» — тему для отдельного исследования: 6–16 слов, понятную без этого отчёта.

=== ОТЧЁТ ===
{report}
=== КОНЕЦ ОТЧЁТА ===

Вопрос: {question}"""

RESEARCH_LINE = re.compile(r"^[\W_]*ИССЛЕДОВАТЬ[\s*_]*:(.+)$", re.M | re.I)


def qa_path(md_path: Path) -> Path:
    return md_path.with_suffix(".qa.json")


def load_qa(md_path: Path) -> list[dict]:
    path = qa_path(md_path)
    return json.loads(path.read_text("utf-8")) if path.exists() else []


async def ask(md_path: Path, question: str) -> dict:
    """Answer and store the question; returns the stored item (q, a, at, cost_rub, maybe research)."""
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    from my_browser import costs

    cfg = Config(str(CONFIG_PATH))
    with costs.metered() as meter:
        answer = await create_chat_completion(
            messages=[{"role": "user", "content": PROMPT.format(report=md_path.read_text("utf-8"), question=question)}],
            model=cfg.smart_llm_model,
            llm_provider=cfg.smart_llm_provider,
            temperature=0.2,
            max_tokens=4000,
            llm_kwargs=cfg.llm_kwargs,
        )
    item = {"q": question, "a": answer.strip(), "at": datetime.now().isoformat(timespec="seconds"),
            "cost_rub": round(meter.rub(), 2)}
    if lines := list(RESEARCH_LINE.finditer(answer)):
        topic = lines[-1].group(1).strip(" .*_«»\"")
        item["a"] = (answer[:lines[-1].start()] + answer[lines[-1].end():]).strip()
        if len(topic) >= 8:
            item["research"] = topic
    qa_path(md_path).write_text(json.dumps(load_qa(md_path) + [item], ensure_ascii=False, indent=2), "utf-8")
    return item
