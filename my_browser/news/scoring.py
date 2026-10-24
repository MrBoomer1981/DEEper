"""Scoring stories with the LLM: is it about AI, its topic, how important, a Russian title and a summary.

Flash scores stories in batches of SCORE_BATCH a call: `is_ai`, `topic` (config.TOPICS), `importance` 0–10 with a
short reason, `title_ru`, and `primary_url` when a post links to the first-hand source (the lab's announcement,
the paper), which also counts as a first-hand source. Stories that are not about AI, or below MIN_IMPORTANCE, are
dropped with the reason. The rest get a summary of one or two sentences in Russian: Pro writes it from PRO_FROM up
(the user's choice: Pro for the main stories), Flash below. A story is scored again only when it gained
RESCORE_GROWTH more independent sources. Every call is metered (common/costs.py) into the spend table, and the
day's limit (a thirtieth of the month's budget) and the month's budget stop the calls.

The same call also catches what merging missed: each story comes with up to SIMILAR_REFS scored stories closest to it
by embeddings, and the model names one in `same_as` when it is the same event (on 24.10.2026 two stories about one
OpenAI round, 0.72 apart, would otherwise both go out, while two different ones were 0.67 apart). Such a story is
merged into the earlier one (status «merged») instead of being scored.
"""

import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import json_repair
import numpy as np

from my_browser.common import costs
from my_browser.common.settings import CONFIG_PATH
from my_browser.news import config, db, dedup

logger = logging.getLogger(__name__)

SCORE_BATCH = 12
SUMMARY_BATCH = 6
MAX_PER_RUN = 60  # stories scored in one run; the first run after a deploy takes the window's backlog
MIN_IMPORTANCE = 4
PRO_FROM = 7
RESCORE_GROWTH = 2
POSTS_PER_STORY = 4
SIMILAR_REFS = 3
SIMILAR_FLOOR = 0.45  # stories farther apart than this are not offered as possibly the same event

SCORE_PROMPT = """Ты редактор ленты новостей об искусственном интеллекте. Ниже сюжеты: в каждом — посты разных источников об одном событии. Сегодня {today}.

Для каждого сюжета определи:
- is_ai: true, если сюжет об ИИ: модели, исследования, продукты и инструменты на ИИ, железо для ИИ, компании ИИ, регулирование и безопасность ИИ. Иначе false.
- topic: одна из тем: models (модели и их выход), research (исследования и статьи), tools (продукты и инструменты), safety (безопасность и риски), policy (законы, суды, регулирование), business (деньги, сделки, компании, люди).
- importance: насколько это важно человеку, который следит за ИИ, от 0 до 10. 9–10 — событие недели: выход ведущей модели, крупнейшая сделка, важный закон. 7–8 — важная новость. 4–6 — заметная. 0–3 — мелочь, реклама, личное мнение без новости, обсуждение, урок или туториал.
- reason: почему такая важность, до 12 слов, по-русски.
- title_ru: заголовок по-русски до 90 знаков: суть события, без кавычек-ёлочек вокруг всего заголовка и без эмодзи. Названия моделей и компаний — как в оригинале.
- primary_url: адрес первоисточника (объявление компании, официальный блог, статья исследователей), если он есть среди ссылок или адресов постов сюжета; иначе null. Пересказ в СМИ — не первоисточник.
- same_as: если сюжет — то же самое событие (тот же релиз, та же сделка, тот же инцидент, то же заявление), что и сюжет из списка «Уже в ленте» под ним, — номер того сюжета, например "S41"; если то же событие, что сюжет этой пачки с меньшим id, — этот id; иначе null. Похожая тема или та же компания — ещё не то же событие.

Ответь только JSON-массивом, по объекту на сюжет, с тем же id:
[{{"id": 1, "is_ai": true, "topic": "models", "importance": 7, "reason": "...", "title_ru": "...", "primary_url": null, "same_as": null}}]

{stories}"""

SUMMARY_PROMPT = """Напиши по-русски резюме каждого сюжета об ИИ: одно-два предложения, 20–45 слов. Только факты из постов: что произошло, кто это сделал, главные цифры. Без оценок, без «по данным источников» и без вводных слов. Названия моделей и компаний оставь как в оригинале.

Ответь только JSON-массивом, по объекту на сюжет, с тем же id:
[{{"id": 1, "summary": "..."}}]

{stories}"""


def _day(settings: config.Settings) -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")


def spent(settings: config.Settings) -> tuple[float, float]:
    """Rubles spent today and this month (days in the settings' time zone)."""
    day = _day(settings)
    with db.connect() as conn:
        today = conn.execute("SELECT COALESCE(SUM(rub), 0) FROM spend WHERE day = ?", (day,)).fetchone()[0]
        month = conn.execute("SELECT COALESCE(SUM(rub), 0) FROM spend WHERE day LIKE ?", (day[:7] + "%",)).fetchone()[0]
    return today, month


def within_budget(settings: config.Settings) -> bool:
    today, month = spent(settings)
    return today < settings.daily_budget_rub and month < settings.budget_month_rub


def _record(settings: config.Settings, rub: float) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO spend (day, rub, calls) VALUES (?, ?, 1) ON CONFLICT (day) DO UPDATE SET "
                     "rub = rub + excluded.rub, calls = calls + 1", (_day(settings), rub))


async def _ask(settings: config.Settings, prompt: str, smart: bool = False) -> list:
    """One metered call; the JSON array of the answer, asked a second time when the first answer is broken."""
    from gpt_researcher.config import Config
    from gpt_researcher.utils.llm import create_chat_completion

    cfg = Config(str(CONFIG_PATH))
    model, provider = (cfg.smart_llm_model, cfg.smart_llm_provider) if smart else (cfg.fast_llm_model, cfg.fast_llm_provider)
    for attempt in range(2):
        with costs.metered() as meter:
            answer = await create_chat_completion(messages=[{"role": "user", "content": prompt}], model=model,
                                                  llm_provider=provider, temperature=0.2, max_tokens=4000,
                                                  llm_kwargs=cfg.llm_kwargs)
        _record(settings, meter.rub(fetch=True))
        match = re.search(r"\[.*\]", answer or "", re.S)
        data = json_repair.loads(match.group(0)) if match else None
        if isinstance(data, list) and data:
            return data
        logger.warning("News scoring: unexpected answer (attempt %d): %r", attempt + 1, (answer or "")[:200])
    return []


def _describe(conn, story: dict, number: int, text_chars: int) -> str:
    posts = conn.execute(
        "SELECT i.title, i.text, i.url, i.links, s.name, s.owner, s.is_primary FROM items i "
        "JOIN sources s ON s.id = i.source_id WHERE i.story_id = ? ORDER BY s.is_primary DESC, i.published",
        (story["id"],)).fetchall()
    chosen, owners = [], set()
    for post in posts:  # one post per owner, first-hand first
        if post["owner"] not in owners:
            chosen.append(post)
            owners.add(post["owner"])
        if len(chosen) == POSTS_PER_STORY:
            break
    links = []
    for post in chosen:
        for link in [post["url"]] + [f"https://{k}" for k in json.loads(post["links"] or "[]")]:
            if link and link not in links:
                links.append(link)
    lines = [f"[id {number}] независимых источников: {story['source_count']}"
             + (", есть первоисточник" if story["has_primary"] else "")]
    for post in chosen:
        text = " ".join((post["text"] or "").split())
        lines.append(f"- {post['name']}{' (первоисточник)' if post['is_primary'] else ''}: {post['title']} — {text[:text_chars]}")
    lines.append("ссылки: " + ", ".join(links[:8]))
    return "\n".join(lines)


def _centroids(conn, story_ids: list[int]) -> dict[int, np.ndarray]:
    """Each story's mean post embedding, normalized (merging stored the posts' embeddings)."""
    if not story_ids:
        return {}
    marks = ",".join("?" * len(story_ids))
    sums: dict[int, np.ndarray] = {}
    for row in conn.execute(f"SELECT story_id, embedding FROM items WHERE story_id IN ({marks}) AND embedding IS NOT NULL",
                            story_ids):
        vector = np.frombuffer(row["embedding"], dtype=np.float32)
        sums[row["story_id"]] = sums.get(row["story_id"], 0) + vector
    return {sid: v / (np.linalg.norm(v) + 1e-9) for sid, v in sums.items()}


def _similar(conn, batch: list[dict], window_start: float) -> dict[int, list[dict]]:
    """Up to SIMILAR_REFS scored stories closest to each story of the batch, for `same_as`."""
    scored = [dict(row) for row in conn.execute(
        "SELECT id, title_ru FROM stories WHERE status IN ('queued', 'sent') AND title_ru IS NOT NULL AND last_updated > ?",
        (window_start,))]
    batch_ids = {s["id"] for s in batch}
    scored = [s for s in scored if s["id"] not in batch_ids]
    vectors = _centroids(conn, [s["id"] for s in scored] + list(batch_ids))
    refs: dict[int, list[dict]] = {}
    for story in batch:
        if story["id"] not in vectors:
            continue
        ranked = sorted(((float(vectors[s["id"]] @ vectors[story["id"]]), s) for s in scored if s["id"] in vectors),
                        key=lambda pair: -pair[0])
        refs[story["id"]] = [s for score, s in ranked[:SIMILAR_REFS] if score >= SIMILAR_FLOOR]
    return refs


def merge_into(conn, source: int, target: int) -> None:
    """Move a story's posts into another story about the same event; the emptied one stays as «merged»."""
    conn.execute("UPDATE items SET story_id = ? WHERE story_id = ?", (target, source))
    conn.execute("UPDATE stories SET status = 'merged', drop_reason = ? WHERE id = ?", (f"тот же сюжет, что #{target}", source))
    dedup.refresh(conn, target)


async def run() -> dict:
    """Score what waits, then summarize what passed; stops at the budget. A summary of the run."""
    settings = config.load()
    if settings.paused or not within_budget(settings):
        return {"scored": 0, "summarized": 0, "budget": not settings.paused}
    now, window = time.time(), settings.dedup_window_hours * 3600
    with db.connect() as conn:
        waiting = [dict(row) for row in conn.execute(
            "SELECT * FROM stories WHERE last_updated > ? AND status != 'merged' "
            "AND (scored_at IS NULL OR source_count >= scored_count + ?) "
            "ORDER BY source_count DESC, last_updated DESC LIMIT ?", (now - window, RESCORE_GROWTH, MAX_PER_RUN))]
    scored = merged = 0
    today = datetime.now(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y")
    for start in range(0, len(waiting), SCORE_BATCH):
        if not within_budget(settings):
            break
        batch = waiting[start:start + SCORE_BATCH]
        with db.connect() as conn:
            refs = _similar(conn, batch, now - window)
            blocks = []
            for n, story in enumerate(batch):
                block = _describe(conn, story, n + 1, 220)
                if refs.get(story["id"]):
                    block += "\nУже в ленте: " + "; ".join(f"[S{r['id']}] {r['title_ru']}" for r in refs[story["id"]])
                blocks.append(block)
        answers = {a.get("id"): a for a in await _ask(settings, SCORE_PROMPT.format(today=today, stories="\n\n".join(blocks)))
                   if isinstance(a, dict)}
        with db.transaction() as conn:
            targets: dict[int, int] = {}  # batch number -> its story id, for same_as within the batch
            for n, story in enumerate(batch):
                answer = answers.get(n + 1)
                if not answer:
                    continue
                target = _same_as(answer.get("same_as"), refs.get(story["id"], []), targets, n + 1)
                if target is not None:
                    merge_into(conn, story["id"], target)
                    merged += 1
                    continue
                _apply(conn, story, answer)
                targets[n + 1] = story["id"]
                scored += 1
    summarized = await _summarize(settings)
    if scored or summarized or merged:
        logger.info("News: %d stories scored, %d merged into earlier ones, %d summarized", scored, merged, summarized)
    return {"scored": scored, "merged": merged, "summarized": summarized}


def _same_as(value, refs: list[dict], targets: dict[int, int], number: int) -> int | None:
    """The story id the model's same_as names: one of the offered «S41», or an earlier story of the batch."""
    if isinstance(value, str) and value.upper().startswith("S") and value[1:].isdigit():
        story_id = int(value[1:])
        return story_id if any(r["id"] == story_id for r in refs) else None
    if isinstance(value, (int, str)) and str(value).isdigit() and int(value) < number:
        return targets.get(int(value))
    return None


def _apply(conn, story: dict, answer: dict) -> None:
    try:
        importance = max(0, min(10, int(answer.get("importance") or 0)))
    except (TypeError, ValueError):
        importance = 0
    is_ai = bool(answer.get("is_ai"))
    topic = answer.get("topic") if answer.get("topic") in config.TOPICS else "business"
    reason = str(answer.get("reason") or "")[:200]
    primary = answer.get("primary_url")
    primary = primary if isinstance(primary, str) and primary.startswith("http") else None
    if story["status"] == "sent":
        status, drop = "sent", None
    elif not is_ai:
        status, drop = "dropped", "не про ИИ"
    elif importance < MIN_IMPORTANCE:
        status, drop = "dropped", f"неважно: {reason}" if reason else "неважно"
    else:
        status, drop = "queued", None
    conn.execute(
        "UPDATE stories SET is_ai = ?, topic = ?, importance = ?, reason = ?, title_ru = ?, primary_url = ?, "
        "has_primary = MAX(has_primary, ?), status = ?, drop_reason = ?, scored_count = source_count, scored_at = ? WHERE id = ?",
        (int(is_ai), topic, importance, reason, str(answer.get("title_ru") or "")[:200] or None, primary,
         int(primary is not None), status, drop, time.time(), story["id"]))


async def _summarize(settings: config.Settings) -> int:
    with db.connect() as conn:
        waiting = [dict(row) for row in conn.execute(
            "SELECT * FROM stories WHERE status IN ('queued', 'sent') AND (summary_ru IS NULL OR summary_ru = '') "
            "AND last_updated > ? ORDER BY importance DESC LIMIT ?", (time.time() - settings.dedup_window_hours * 3600, MAX_PER_RUN))]
    done = 0
    for smart in (True, False):
        group = [s for s in waiting if (s["importance"] or 0) >= PRO_FROM] if smart else \
            [s for s in waiting if (s["importance"] or 0) < PRO_FROM]
        for start in range(0, len(group), SUMMARY_BATCH):
            if not within_budget(settings):
                return done
            batch = group[start:start + SUMMARY_BATCH]
            with db.connect() as conn:
                listing = "\n\n".join(_describe(conn, story, n + 1, 600) for n, story in enumerate(batch))
            answers = {a.get("id"): a for a in await _ask(settings, SUMMARY_PROMPT.format(stories=listing), smart=smart)
                       if isinstance(a, dict)}
            with db.transaction() as conn:
                for n, story in enumerate(batch):
                    summary = str((answers.get(n + 1) or {}).get("summary") or "").strip()
                    if summary:
                        conn.execute("UPDATE stories SET summary_ru = ? WHERE id = ?", (summary[:600], story["id"]))
                        done += 1
    return done
