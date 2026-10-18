"""Suggestions around a research run, made by the cheap FAST_LLM.

Before a run, «Уточнить» asks 2-3 questions with ready answers and rewrites the topic with the
chosen ones. After a run, «Что исследовать дальше» proposes five follow-up topics from the
report's outline, conclusions and limitations. save() stores them in the report's JSON and
appends them to its files as the last section; body() strips that section for the web view.
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

import json_repair

from my_browser import costs
from my_browser.pipeline import CONFIG_PATH

logger = logging.getLogger(__name__)

KINDS = {"deeper": "Углубить", "adjacent": "Рядом", "verify": "Проверить"}
FOLLOWUP_MODES = ("quick", "detailed", "deep")
SECTION_TITLE = "## Что исследовать дальше"
ANY_ANSWER = "не важно"

FOLLOWUPS_PROMPT = """Ты планируешь следующие исследования. Ниже выдержки из готового отчёта на тему «{query}»: оглавление, выводы и разделы об ограничениях. Сегодня {today}.

Предложи 5 тем для новых исследований, которые продолжают этот отчёт:
- 2 темы kind="deeper": подтема, которую отчёт затронул вскользь или где ему не хватило данных;
- 2 темы kind="adjacent": смежная область, без которой картина неполна;
- 1 тема kind="verify": где источники расходятся, цифры спорные, есть риски или сильные контраргументы.

Поле query — самостоятельный запрос для нового исследования: понятен без этого отчёта (предмет назван явно), по-русски, 6–16 слов, без кавычек, не повторяет тему отчёта и не пересказывает его разделы.
Поле why — зачем это исследовать: одна фраза до 100 знаков.
Поле mode — "quick" для узкого вопроса, "detailed" для широкой темы.

Ответь только JSON-массивом:
[{{"kind": "deeper", "query": "...", "why": "...", "mode": "quick"}}]

Оглавление:
{outline}

Выдержки:
{excerpt}"""

CLARIFY_PROMPT = """Пользователь заказывает длинный исследовательский отчёт на тему «{query}». Сегодня {today}.
Задай от 1 до 3 уточняющих вопросов, ответы на которые сильнее всего изменят содержание отчёта: период, регион или рынок, цель отчёта, конкретный аспект. Не спрашивай о том, что уже ясно из темы.
К каждому вопросу дай 3–4 варианта ответа, каждый до 5 слов. Вариант «не важно» не добавляй: он есть всегда.

Ответь только JSON-массивом:
[{{"q": "Вопрос?", "options": ["вариант", "вариант", "вариант"]}}]"""

REFINE_PROMPT = """Перепиши тему исследования одной формулировкой с учётом ответов пользователя на уточняющие вопросы. Сохрани исходный смысл и встрой ответы естественно, без списков и без слова «уточнение». По-русски, до 30 слов. Ответь только новой темой.

Тема: {query}
Ответы:
{answers}"""

HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$", re.M)
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
REFERENCES = re.compile(r"источник|литератур|ссылк|reference|bibliograph", re.I)
TABLE_OF_CONTENTS = re.compile(r"оглавлен|содержани|table of contents", re.I)
SUMMARY = re.compile(r"заключени|вывод|итог|резюме|conclusion|summary", re.I)
GAPS = re.compile(
    r"ограничени|пробел|открыт|риск|вызов|барьер|проблем|неопредел|спорн|противореч|"
    r"limitation|gap|open question|risk|challenge|barrier|uncertaint",
    re.I,
)


async def fast(prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
    """One cheap FAST_LLM call (eval/run.py uses it for its judges too)."""
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    cfg = Config(str(CONFIG_PATH))
    return await create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=cfg.fast_llm_model,
        llm_provider=cfg.fast_llm_provider,
        temperature=temperature,
        max_tokens=max_tokens,
        llm_kwargs=cfg.llm_kwargs,
    )


def json_list(text: str) -> list:
    """The JSON array in a model answer, repaired if needed ({"topics": [...]} also works)."""
    match = re.search(r"\[.*\]", text, re.S)
    data = json_repair.loads(match.group(0) if match else text)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return data if isinstance(data, list) else []


def body(markdown: str) -> str:
    """The report without the follow-ups section that save() appends."""
    head, found, _ = markdown.rpartition(f"\n{SECTION_TITLE}\n")
    return head.rstrip() if found else markdown


def section(topics: list[dict]) -> str:
    if not topics:
        return ""
    items = "\n".join(f"- **{KINDS[t['kind']]}.** {t['query']}" + (f" — {t['why']}" if t["why"] else "") for t in topics)
    return f"\n\n{SECTION_TITLE}\n\n{items}\n"


def _excerpt(markdown: str, max_words: int = 1500, section_words: int = 700) -> tuple[str, str]:
    """Outline plus the conclusions, then the limitation sections, without links and references."""
    text = LINK.sub(r"\1", body(markdown))
    headings = list(HEADING.finditer(text))
    references = next((m for m in headings if len(m.group(1)) <= 2 and REFERENCES.search(m.group(2))
                       and m.start() > len(text) // 2), None)
    if references:
        text = text[:references.start()]
        headings = [m for m in headings if m.start() < references.start()]
    outline = "\n".join(f"{'  ' * (len(m.group(1)) - 1)}- {m.group(2)}" for m in headings
                        if not TABLE_OF_CONTENTS.search(m.group(2)))

    summaries, gaps, covered_until = [], [], -1
    for i, m in enumerate(headings):
        title, level = m.group(2), len(m.group(1))
        if level == 1 or m.start() < covered_until or not (SUMMARY.search(title) or GAPS.search(title)):
            continue  # level 1 is the report's title and would cover the whole text
        end = next((h.start() for h in headings[i + 1:] if len(h.group(1)) <= level), len(text))
        section_text = " ".join(text[m.end():end].split()[:section_words])
        (summaries if SUMMARY.search(title) else gaps).append(f"{title}\n{section_text}")
        covered_until = end
    if summaries or gaps:
        excerpt = " ".join("\n\n".join(summaries + gaps).split(" ")[:max_words])
    else:  # no conclusion heading: the end of the report usually sums it up
        excerpt = " ".join(text.split()[-max_words:])
    return "\n".join(outline.splitlines()[:80]), excerpt


async def followups(query: str, markdown: str) -> list[dict]:
    """Five next research topics. Any failure gives [], so the report is saved regardless."""
    outline, excerpt = _excerpt(markdown)
    prompt = FOLLOWUPS_PROMPT.format(query=query, today=date.today().isoformat(), outline=outline or "—", excerpt=excerpt)
    try:
        items = json_list(await fast(prompt, max_tokens=1500))
    except Exception as e:
        logger.warning("Follow-up topics failed: %s", e)
        return []
    topics, seen = [], {query.strip().lower()}
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        text = str(item.get("query") or "").strip().strip("«»\"").strip()
        if len(text) < 8 or text.lower() in seen:
            continue
        seen.add(text.lower())
        mode = item.get("mode") if item.get("mode") in FOLLOWUP_MODES else "quick"
        topics.append({"kind": item["kind"], "query": text, "why": str(item.get("why") or "").strip(), "mode": mode})
    topics.sort(key=lambda t: list(KINDS).index(t["kind"]))
    return topics[:5]


async def followups_for(json_path: Path) -> list[dict]:
    """Follow-ups for a report saved before they existed; kept in its JSON, the files stay as they are."""
    meta = json.loads(json_path.read_text("utf-8"))
    with costs.metered() as meter:
        topics = await followups(meta["query"], json_path.with_suffix(".md").read_text("utf-8"))
    if topics:
        meta["followups"] = topics
        meta["followups_cost_rub"] = round(meter.rub(), 2)
        json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return topics


async def questions(query: str) -> tuple[list[dict], float]:
    """Up to 3 clarifying questions with ready answers, and their cost in rubles."""
    with costs.metered() as meter:
        answer = await fast(CLARIFY_PROMPT.format(query=query, today=date.today().isoformat()), max_tokens=800)
    result = []
    for item in json_list(answer):
        if not isinstance(item, dict) or not str(item.get("q") or "").strip() or not isinstance(item.get("options"), list):
            continue
        options = [str(o).strip() for o in item["options"] if str(o).strip() and str(o).strip().lower() != ANY_ANSWER]
        if len(options) >= 2:
            result.append({"q": str(item["q"]).strip(), "options": options[:4]})
    return result[:3], meter.rub()


async def refine(query: str, answers: list[tuple[str, str]]) -> tuple[str, float]:
    """The topic rewritten with the chosen answers, and the cost in rubles."""
    lines = "\n".join(f"- {question} — {answer}" for question, answer in answers)
    with costs.metered() as meter:
        text = await fast(REFINE_PROMPT.format(query=query, answers=lines), max_tokens=200, temperature=0.2)
    refined = text.strip().strip("«»\"").strip().rstrip(".")
    return (refined if 8 <= len(refined) <= 400 else query), meter.rub()
