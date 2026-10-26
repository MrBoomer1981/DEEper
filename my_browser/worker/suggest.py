"""Suggestions around a research run, made by the cheap FAST_LLM.

Before a run, «Уточнить» asks 2-3 questions with ready answers and rewrites the topic with the
chosen ones. After a run, «Что исследовать дальше» proposes four follow-up topics of different kinds
(common.report.FOLLOWUP_KINDS) from the report's outline, summary and open questions. It knows the
branch the report belongs to, so it does not offer what the reader has read or the route will cover, and
the reader's profile, for «Для меня». The first report of a branch also gets a route: 3-5 next steps
through its topic, when the topic deserves them. The worker's job loop and its API call these, and core
stores the topics and the route (common/report.py renders and strips the topics' section).
"""

import logging
import re
from datetime import date

import json_repair

from my_browser.common import costs
from my_browser.common.report import ANY_ANSWER, FOLLOWUP_KINDS, KINDS, body
from my_browser.common.settings import CONFIG_PATH, DEFAULT_MODE

logger = logging.getLogger(__name__)

FOLLOWUPS = 4
ROUTE_STEPS = (3, 5)  # steps after the first report; fewer than 3 is no route

FOLLOWUPS_PROMPT = """Ты подбираешь, что читателю исследовать дальше. Ниже выдержки из готового отчёта на тему «{query}»: оглавление, главное и открытые вопросы. Каждое продолжение — новый короткий отчёт на 5–7 минут чтения. Сегодня {today}.
{known}
Предложи {count} темы продолжений, каждую своего вида:
- kind="deeper" (Глубже) — обязательно: то, что отчёт затронул вскользь или где ему не хватило данных;
- kind="against" (Против) — обязательно: самые сильные контраргументы, критика, риски, где источники расходятся;
- и две из этих:
  - kind="practice" (На практике): как это применить, по шагам, инструменты, цена, кто уже так делает;
  - kind="personal" (Для меня): как тема связана с читателем — только если выше есть сведения о нём и связь не натянута;
  - kind="fresh" (Свежее): что изменилось за последние месяцы — только если область быстро меняется;
  - kind="adjacent" (Рядом): смежная область, без которой картина неполна.

Поле query — самостоятельный запрос: понятен без этого отчёта (предмет назван явно), по-русски, 6–16 слов, без кавычек, не повторяет тему отчёта, его разделы и то, что читатель уже прочитал{route_rule}.
Поле why — зачем это читать: одна фраза до 100 знаков.
{route_ask}
Ответь только JSON:
{{"topics": [{{"kind": "deeper", "query": "...", "why": "..."}}]{route_shape}}}

Оглавление:
{outline}

Выдержки:
{excerpt}"""

ROUTE_ASK = """
Ещё составь маршрут — короткий курс по теме «{query}» из {low}–{high} следующих шагов, если тема достаточно широкая, чтобы её стоило проходить по шагам; иначе пустой список. Этот отчёт — шаг 1. Шаги идут в логичном порядке, от основ к выводам и применению, не повторяют друг друга и темы продолжений; query и why шага — как у тем.
"""

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
SUMMARY = re.compile(r"главн|заключени|вывод|итог|резюме|key points|conclusion|summary", re.I)
GAPS = re.compile(
    r"не знаем|ограничени|пробел|открыт|риск|вызов|барьер|проблем|неопредел|спорн|противореч|"
    r"don't know|limitation|gap|open question|risk|challenge|barrier|uncertaint",
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


def json_object(text: str) -> dict:
    """The JSON object in a model answer, repaired if needed; a bare array counts as its "topics"."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return {}
    data = json_repair.loads(text[min(starts):])
    if isinstance(data, list):
        return {"topics": data}
    return data if isinstance(data, dict) else {}


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


def _known(branch: list[dict], route: list[dict], profile: str) -> str:
    """What the follow-ups must know besides the report: the branch read before it, the route, the reader."""
    parts = []
    if branch:
        parts.append("Читатель уже прочитал в этой ветке (ближайшее первым):\n"
                     + "\n".join(f"- «{b['query']}»" for b in branch))
    if route:
        parts.append("Маршрут, по которому идёт читатель (шаги — отдельные отчёты):\n"
                     + "\n".join(f"{n}. {s['query']}" for n, s in enumerate(route, 1)))
    if profile:
        parts.append(f"О читателе: {profile}")
    return "".join(f"\n{part}\n" for part in parts)


KIND_OF_LABEL = {label.lower(): kind for kind, label in KINDS.items()}  # a model may answer «Глубже» for "deeper"


def _kind(value) -> str | None:
    """The follow-up kind a model meant: the key itself, its label, or either with something around it."""
    text = str(value or "").strip().lower()
    if text in FOLLOWUP_KINDS:
        return text
    if text in KIND_OF_LABEL:
        return KIND_OF_LABEL[text] if KIND_OF_LABEL[text] in FOLLOWUP_KINDS else None
    return next((kind for kind in FOLLOWUP_KINDS if kind in text), None) or \
        next((kind for label, kind in KIND_OF_LABEL.items() if label in text and kind in FOLLOWUP_KINDS), None)


def _topic(item, seen: set[str], kinds: bool) -> dict | None:
    """A topic (`kinds`: with its kind) or a route's step, None when it is malformed or a repeat."""
    kind = _kind(item.get("kind")) if isinstance(item, dict) and kinds else None
    if not isinstance(item, dict) or (kinds and kind is None):
        return None
    text = str(item.get("query") or "").strip().strip("«»\"").strip()
    if len(text) < 8 or text.lower() in seen:
        return None
    seen.add(text.lower())
    text = text[0].upper() + text[1:]
    return {"kind": kind, "query": text, "why": str(item.get("why") or "").strip(), "mode": DEFAULT_MODE}


async def followups(query: str, markdown: str, branch: list[dict] | None = None, profile: str = "",
                    route: list[dict] | None = None, make_route: bool = False) -> dict:
    """{"topics": [...], "route": [...]}: FOLLOWUPS next topics of different kinds and, if `make_route`, the route's
    next steps ({"query", "why"}). `branch` is the reports read before this one ({"query"}, nearest first), `route`
    the steps of the route the branch follows. Any failure gives empty lists, so the report is saved regardless."""
    outline, excerpt = _excerpt(markdown)
    low, high = ROUTE_STEPS
    prompt = FOLLOWUPS_PROMPT.format(
        query=query, today=date.today().isoformat(), outline=outline or "—", excerpt=excerpt, count=FOLLOWUPS,
        known=_known(branch or [], route or [], profile.strip()),
        route_rule=" и шаги маршрута" if route else "",
        route_ask=ROUTE_ASK.format(query=query, low=low, high=high) if make_route else "",
        route_shape=', "route": [{"query": "...", "why": "..."}]' if make_route else "")
    for attempt in (1, 2):  # a second try when the answer gave fewer than two usable topics (about 0.05 ₽)
        try:
            answer = await fast(prompt, max_tokens=1500)
            data = json_object(answer)
        except Exception as e:
            logger.warning("Follow-up topics failed: %s", e)
            return {"topics": [], "route": []}
        seen = {query.strip().lower(), *(b["query"].strip().lower() for b in branch or []),
                *(s["query"].strip().lower() for s in route or [])}
        topics = [t for item in data.get("topics") or [] if (t := _topic(item, seen, True))]  # topics first: a step
        steps = [s for item in (data.get("route") or [] if make_route else []) if (s := _topic(item, seen, False))]  # repeating one goes
        unique = list({t["kind"]: t for t in reversed(topics)}.values())[::-1]  # one topic of each kind, the first one
        if len(unique) >= 2:
            break
        logger.warning("Follow-up topics: %d usable in %r", len(unique), answer[:600])
    unique.sort(key=lambda t: list(KINDS).index(t["kind"]))
    return {"topics": unique[:FOLLOWUPS],
            "route": [{"query": s["query"], "why": s["why"]} for s in steps[:high]] if len(steps) >= low else []}


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
