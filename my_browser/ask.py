"""Follow-up questions about a saved report, answered from its text only."""

from pathlib import Path

from my_browser.pipeline import CONFIG_PATH

PROMPT = """Ниже полный текст исследовательского отчёта. Ответь на вопрос пользователя, опираясь только на этот отчёт.
Если в отчёте нет ответа, прямо скажи об этом и подскажи, что стоит исследовать отдельно.
Отвечай по-русски, по существу, без вступлений. Сохраняй ссылки на источники из отчёта, если они относятся к ответу.

=== ОТЧЁТ ===
{report}
=== КОНЕЦ ОТЧЁТА ===

Вопрос: {question}"""


async def ask(md_path: Path, question: str) -> str:
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    cfg = Config(str(CONFIG_PATH))
    return await create_chat_completion(
        messages=[{"role": "user", "content": PROMPT.format(report=md_path.read_text("utf-8"), question=question)}],
        model=cfg.smart_llm_model,
        llm_provider=cfg.smart_llm_provider,
        temperature=0.2,
        max_tokens=4000,
        llm_kwargs=cfg.llm_kwargs,
    )
