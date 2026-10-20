"""A short version of a report, at most SHORT_WORDS words, to read on a phone («⋯» → «Сократить до 1 500 слов»).

The writer model (SMART_LLM, Pro by default) condenses the report's text: the title, the sections that matter,
the findings, the key facts and figures with their citations, and nothing the report does not say. The list of
sources is then built from the links the short version cites. Core keeps the result next to the report as
<name>.short.md; it is written once per report.
"""

import re

from my_browser.common.report import body
from my_browser.common.settings import CONFIG_PATH
from my_browser.common.text import MARKDOWN_LINK

SHORT_WORDS = 1500
# A list of sources the model writes anyway: a heading that is only that, so «## Источники энергии» stays.
SOURCES_HEADING = re.compile(r"^#{1,3}\s*(?:список\s+)?(?:источники|источников|литература|литературы|ссылки|references|sources|bibliography)\s*:?\s*$",
                             re.I | re.M)

# The target is well below the limit: on a 7 500-word report Pro wrote 1 603 words for «около 1 400, не больше 1 500».
PROMPT = """Ниже полный текст исследовательского отчёта. Сократи его до краткой версии для чтения с телефона: около {target} слов и ни в коем случае не больше {words}.
- Первая строка — заголовок отчёта («# …»). Дальше разделы «## …» в том же порядке; мелкие объедини или убери.
- Оставь главное: выводы, ключевые факты и числа, сравнения, риски и ограничения. Убери повторы, общий фон, второстепенные примеры, оглавление и список источников.
- Ссылки: у каждого факта, который остаётся, сохрани его ссылку из отчёта в том же виде ([источник](url)), сразу после предложения. Не добавляй фактов, чисел, ссылок и источников, которых нет в отчёте. Свои обобщения пиши без ссылок.
- Пиши на языке отчёта. Верни только текст краткой версии в Markdown, без вступлений и пояснений.

=== ОТЧЁТ ===
{report}
=== КОНЕЦ ОТЧЁТА ==="""

# When the short version still runs over: once more, from its own text, which costs a fraction of the first pass.
FIT = """Ниже краткая версия отчёта, но в ней больше {words} слов. Сократи её до {target} слов, не больше {words}: убери второстепенные подробности и повторы. Заголовок, разделы и ссылки ([источник](url)) оставь как есть, ничего нового не добавляй. Верни только текст в Markdown.

=== ТЕКСТ ===
{text}
=== КОНЕЦ ТЕКСТА ==="""


async def _write(cfg, prompt: str) -> str:
    """One call to the writer model; a list of sources it writes anyway goes, the cited links make it."""
    from gpt_researcher.utils.llm import create_chat_completion

    text = (await create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=cfg.smart_llm_model,
        llm_provider=cfg.smart_llm_provider,
        temperature=0.3,
        max_tokens=8000,
        llm_kwargs=cfg.llm_kwargs,
    )).strip()
    if found := list(SOURCES_HEADING.finditer(text)):
        text = text[:found[-1].start()].rstrip()
    return text


async def shorten(markdown: str, words: int = SHORT_WORDS) -> dict:
    """{markdown, words, cost_rub}: the short version and its list of sources; `words` counts the text without the list."""
    from gpt_researcher.config import Config

    from my_browser.common import costs

    cfg = Config(str(CONFIG_PATH))
    target = round(words * 0.8 / 100) * 100
    with costs.metered() as meter:
        text = await _write(cfg, PROMPT.format(target=target, words=words, report=body(markdown)))
        if len(text.split()) > words:
            text = await _write(cfg, FIT.format(target=target, words=words, text=text))
    count = len(text.split())
    cited = list(dict.fromkeys(m.group(2) for m in MARKDOWN_LINK.finditer(text)))
    if cited:
        heading = "Источники" if cfg.language.lower() == "russian" else "References"
        text += f"\n\n## {heading}\n\n" + "".join(f"- [{url}]({url})\n" for url in cited)
    return {"markdown": text, "words": count, "cost_rub": round(meter.rub(), 2)}
