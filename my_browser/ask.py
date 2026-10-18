"""Follow-up questions about a saved report, answered from its text only.

The questions and answers are kept next to the report as <name>.qa.json, so the
Telegram bot and the web app share them.
"""

import json
from datetime import datetime
from pathlib import Path

from my_browser.pipeline import CONFIG_PATH

PROMPT = """Ниже полный текст исследовательского отчёта. Ответь на вопрос пользователя, опираясь только на этот отчёт.
Если в отчёте нет ответа, прямо скажи об этом и подскажи, что стоит исследовать отдельно.
Отвечай по-русски, по существу, без вступлений. Сохраняй ссылки на источники из отчёта, если они относятся к ответу.

=== ОТЧЁТ ===
{report}
=== КОНЕЦ ОТЧЁТА ===

Вопрос: {question}"""


def qa_path(md_path: Path) -> Path:
    return md_path.with_suffix(".qa.json")


def load_qa(md_path: Path) -> list[dict]:
    path = qa_path(md_path)
    return json.loads(path.read_text("utf-8")) if path.exists() else []


async def ask(md_path: Path, question: str) -> str:
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    cfg = Config(str(CONFIG_PATH))
    answer = await create_chat_completion(
        messages=[{"role": "user", "content": PROMPT.format(report=md_path.read_text("utf-8"), question=question)}],
        model=cfg.smart_llm_model,
        llm_provider=cfg.smart_llm_provider,
        temperature=0.2,
        max_tokens=4000,
        llm_kwargs=cfg.llm_kwargs,
    )
    history = load_qa(md_path) + [{"q": question, "a": answer, "at": datetime.now().isoformat(timespec="seconds")}]
    qa_path(md_path).write_text(json.dumps(history, ensure_ascii=False, indent=2), "utf-8")
    return answer
