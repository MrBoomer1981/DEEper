"""Follow-up questions about a report, answered from its text only.

The worker answers (POST /ask); core keeps the history next to the report as <name>.qa.json.
When the report has no answer, the model names a topic for a separate research, which both
UIs offer to run.
"""

import re

from my_browser.common.settings import CONFIG_PATH

PROMPT = """Ниже полный текст исследовательского отчёта. Ответь на вопрос пользователя, опираясь только на этот отчёт.
Отвечай по-русски, по существу, без вступлений. Сохраняй ссылки на источники из отчёта, если они относятся к ответу.
Если в отчёте нет ответа или он неполный, прямо скажи об этом и последней строкой напиши «ИССЛЕДОВАТЬ: тема» — тему для отдельного исследования: 6–16 слов, понятную без этого отчёта.

=== ОТЧЁТ ===
{report}
=== КОНЕЦ ОТЧЁТА ===

Вопрос: {question}"""

RESEARCH_LINE = re.compile(r"^[\W_]*ИССЛЕДОВАТЬ[\s*_]*:(.+)$", re.M | re.I)


async def answer(markdown: str, question: str) -> dict:
    """{a, cost_rub} and, when the report lacks the answer, research: a topic to look into separately."""
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    from my_browser.common import costs

    cfg = Config(str(CONFIG_PATH))
    with costs.metered() as meter:
        text = await create_chat_completion(
            messages=[{"role": "user", "content": PROMPT.format(report=markdown, question=question)}],
            model=cfg.smart_llm_model,
            llm_provider=cfg.smart_llm_provider,
            temperature=0.2,
            max_tokens=4000,
            llm_kwargs=cfg.llm_kwargs,
        )
    item = {"a": text.strip(), "cost_rub": round(meter.rub(), 2)}
    if lines := list(RESEARCH_LINE.finditer(text)):
        topic = lines[-1].group(1).strip(" .*_«»\"")
        item["a"] = (text[:lines[-1].start()] + text[lines[-1].end():]).strip()
        if len(topic) >= 8:
            item["research"] = topic
    return item
