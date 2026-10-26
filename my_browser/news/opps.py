"""Opportunities: what each story of the opps lane is, read by the LLM (Flash) within the lane's own budget.

1. Roundups are cut first (split()): a post gathering several announcements (a channel's «5 хакатонов недели», an
   aggregator's list, a weekly digest of programs) becomes one post per opportunity, each with its own link; the next
   merging run places them like any post. A roundup with nothing to apply to gives nothing.
2. Each waiting story (never read, or grown since: a new post may carry a moved deadline) is read with up to
   POSTS_PER_STORY posts, EXTRACT_BATCH a call (extract()): is it an opportunity at all; its type, Russian title,
   organizer, what it gives; the deadline as the source writes it and as a date; the event's dates, format, place, who
   may apply, whether a Russian citizen living in Russia may take part, cost, reward, where to apply; how well it fits
   the user's profile (config.clean_profile(): no contacts) and why; whether it is especially big (`major`: prepared
   for months ahead, so the app shows it within its longer window). The same call names `same_as`: one of up to
   SIMILAR_REFS opportunities closest by embeddings, when it is the same one told in other words (merging leaves
   retellings apart, dedup.OPPS_THRESHOLD); the story is then merged into it. An opportunity read before `major`
   existed is read again once, without a «deadline moved» message: its posts did not change.
3. Dates are checked by the code, not trusted (deadline()): an ISO date or date and time, the time zone from the text,
   else Moscow for a Russian source and UTC for an international one (noted as assumed), the end of the day when no
   time is given. «до конца ноября» is `approximate`, its last day; «круглый год» is `rolling`, without a date.
A story whose posts carry no sign of an announcement at all (SIGNAL: registration, applications, a deadline, a
hackathon, an internship…) is not sent to the model: on the collection of 25.10.2026 that was a quarter of the
stories (tech blog posts, greetings, lectures, results), and none of the labeled opportunities of eval/opps.
Pages, Devpost and awesome-mlss always go to the model.
Not an opportunity: `dropped` with the reason; a deadline already past, or applications the text calls closed:
`expired` (every run looks again); a duplicate: its story `merged`. A low fit or no access from Russia drops nothing, it only goes down the list. A
tracked opportunity whose deadline moved gets a message. Every call is metered into opps_spend: the lane's budget,
apart from the news (scoring.within_budget(settings, "opps")).
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from my_browser.news import config, db, items, scoring
from my_browser.news.collectors import clean_url, link_key

logger = logging.getLogger(__name__)

SPLIT_CHARS = 9000  # roundups per split call, by their length: a long list goes alone
MAX_SPLITS_PER_RUN = 30
EXTRACT_BATCH = 6
MAX_PER_RUN = 60  # stories read in one run; the first runs after a deploy take a month of announcements
POSTS_PER_STORY = 3
POST_CHARS = {"page": 3500}  # a program's page holds its conditions; other posts are short
OTHER_CHARS = 1500
SIMILAR_REFS = 3
SIMILAR_FLOOR = 0.55  # retellings of one hackathon were 0.74 apart on 25.10.2026
KINDS = ("exact", "approximate", "rolling", "unknown")
FORMATS = ("online", "offline", "hybrid")
ACCESS = ("yes", "no", "unclear")
USER_STATUSES = ("tracked", "hidden", "applied")  # set by the user: extraction never changes them back
MOSCOW, UTC = ZoneInfo("Europe/Moscow"), timezone.utc
ZONES = {"aoe": timezone(timedelta(hours=-12)), "utc": UTC, "gmt": UTC, "z": UTC, "msk": MOSCOW, "мск": MOSCOW,
         "москва": MOSCOW, "pt": ZoneInfo("America/Los_Angeles"), "pst": ZoneInfo("America/Los_Angeles"),
         "pdt": ZoneInfo("America/Los_Angeles"), "et": ZoneInfo("America/New_York"), "est": ZoneInfo("America/New_York"),
         "edt": ZoneInfo("America/New_York"), "cet": ZoneInfo("Europe/Berlin"), "cest": ZoneInfo("Europe/Berlin"),
         "bst": ZoneInfo("Europe/London")}
OFFSET = re.compile(r"^(?:utc|gmt)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$")
SIGNAL = re.compile(
    r"регистр|заявк|подать|подач|отбор|дедлайн|конкурс|хакатон|чемпионат|олимпиад|соревнован|стажир|стипенди|грант|"
    r"поступ|набор |приём|прием|интенсив|летн\w* школ|зимн\w* школ|кейс|call for|apply|applica|deadline|regist|submi|"
    r"hackathon|competition|challenge|internship|fellowship|scholarship|grant|admission|school|cohort|enrol|prize|award",
    re.IGNORECASE)
ALWAYS_READ = ("page", "devpost", "mlss")  # sources made of opportunities: never filtered

SPLIT_PROMPT = """Ты разбираешь посты-подборки о возможностях для студентов: в одном посте или на одной странице собрано несколько анонсов. Сегодня {today}.

Выпиши из каждой подборки каждую отдельную возможность, куда можно подать заявку или зарегистрироваться для участия с отбором, конкурсом или приёмом работ: хакатон, соревнование, олимпиада, кейс-чемпионат, школа или интенсив с отбором, стажировка, исследовательская программа, программа с поступлением, конференция или воркшоп с приёмом работ, форум или конгресс для студентов и молодых учёных с заявкой на участие, которую рассматривают или утверждают, грант, стипендия, конкурс, набор докладчиков, экзамен с регистрацией. Не выписывай новости, итоги и записи прошедших событий, мероприятия, куда приходят только зрителем или слушателем без отбора (посмотреть выступления, лекции, показы, слэмы), вакансии на постоянную работу, курсы и лекции без отбора, вебинары и митапы для слушателей, рекламу.

Для каждой возможности:
- title: название, как в подборке;
- text: 2–4 предложения из подборки об этой возможности — что это, кто проводит, сроки и дедлайн, формат, для кого, призы; только то, что написано, ничего не добавляй;
- url: адрес этой возможности из текста подборки (адреса стоят в скобках после текста ссылки или в списке ссылок), если он там есть; иначе null.

Ответь только JSON-массивом, по объекту на подборку, с тем же номером; если возможностей нет — пустой items:
[{{"post": 1, "items": [{{"title": "...", "text": "...", "url": "https://..."}}]}}]

{posts}"""

EXTRACT_PROMPT = """Ты разбираешь анонсы возможностей для студента: хакатоны, соревнования, школы, стажировки, программы, гранты, конференции. Сегодня {today}. Ниже профиль студента и сюжеты: в каждом — посты разных источников об одном и том же.

Профиль студента: {profile}

Для каждого сюжета определи:
- is_opportunity: true, если это анонс того, куда можно подать заявку или зарегистрироваться для участия с отбором, конкурсом или приёмом работ: хакатон, соревнование, олимпиада, кейс-чемпионат, школа или интенсив с отбором, стажировка, исследовательская программа, программа с поступлением, конференция или воркшоп с приёмом работ, форум или конгресс для студентов и молодых учёных с участием по заявке, которую рассматривают или утверждают (Конгресс молодых учёных, молодёжные форумы с отбором), грант, стипендия, конкурс проектов, набор докладчиков, экзамен с регистрацией. Страница программы, приём в которую сейчас закрыт или ещё не открыт (есть только форма «сообщить об открытии»), — тоже true. false — новость, итоги, мнение, реклама курса без отбора, лекция, вебинар, митап или шоу, куда приходят слушателем или зрителем без отбора (регистрация только чтобы попасть в зал), вакансия или найм опытных специалистов (middle, senior, в том числе ускоренный найм — fast track). Если false, остальные поля — null, кроме reason.
- reason: если false — почему, до 8 слов; иначе null.
- opp_type: hackathon (хакатон), competition (соревнование по данным, олимпиада, контест, кейс-чемпионат), school (летняя или зимняя школа, интенсив с отбором), internship (стажировка, студенческая программа компании), fellowship (исследовательская программа: MATS и подобные), education (поступление: ШАД, магистратура, курс с отбором), conference (конференция, конгресс или форум с заявкой на участие, воркшоп, call for papers), grant (грант, стипендия, конкурс проектов), talk (выступление: Science Slam, набор докладчиков), exam (экзамен, сертификат), other.
- title_ru: название по-русски до 80 знаков; имена собственные (MATS, Yandex Cup) — как в оригинале.
- organizer: кто проводит.
- summary_ru: что это и что даёт участнику, одно-два предложения по-русски.
- deadline_text: дедлайн подачи заявки или регистрации так, как он написан в источнике («до 1 ноября», «Apply by Nov 1, 11:59 PM AoE»); null, если его нет.
- deadline: тот же дедлайн в ISO — "2026-11-01" или с временем "2026-11-01T23:59"; null, если даты нет. Конец срока приёма работ («Submission period: Oct 1 - Nov 12», «приём работ до 12 ноября») — тоже дедлайн. Если указан только месяц («до конца ноября») — последний день месяца. Год, если не указан, — ближайший будущий от сегодняшней даты. Если дедлайна подачи нет, а есть только даты самого события — null.
- deadline_tz: часовой пояс дедлайна, если он указан в тексте ("Europe/Moscow", "UTC", "AoE", "PT"); иначе null.
- deadline_kind: exact (точная дата), approximate (только месяц или «примерно»), rolling (приём круглый год или до заполнения мест), unknown (дедлайн не указан).
- closed: true, только если в тексте прямо сказано, что приём заявок закрыт или завершён («applications are closed», «регистрация завершена»); дату дедлайна в прошлом сверит код, это не повод для true. Иначе false.
- starts, ends: даты самого события в ISO, если указаны; иначе null.
- format: online, offline или hybrid; null, если не сказано.
- location: город и страна, если офлайн; иначе null.
- eligibility: кто может подать, коротко (студенты, возраст, страна, уровень).
- ru_access: может ли участвовать гражданин РФ, живущий в России: yes — российская возможность или явно открыта всем странам без оговорок; no — Россия или её граждане исключены, или только для граждан или жителей других стран; unclear — не сказано, или есть оговорка о странах под санкциями.
- cost: бесплатно или цена участия; null, если не сказано.
- reward: призы, стипендия или оплата; null, если нет.
- apply_url: адрес подачи заявки или официальная страница из адресов сюжета; null, если их нет.
- fit: насколько это подходит профилю студента, от 0 до 10. 9–10 — точно его тема и формат; 6–8 — хорошо подходит; 3–5 — отчасти; 0–2 — не его профиль. Доступность из России в fit не учитывай: для неё есть ru_access.
- fit_reason: почему такой fit, до 15 слов, по-русски, обращаясь на «ты».
- major: true, если это особо крупная возможность, к которой готовятся заранее: международного или всероссийского масштаба, от известного организатора (крупная компания, ведущий университет, известная программа), с большим призовым фондом, стипендией или оплатой, или с отбором в несколько этапов (эссе, рекомендации, тестовое задание, собеседование). Например: MATS, ШАД, Yandex Cup, ICPC, летние школы ML с отбором, стажировки больших компаний. Обычный хакатон выходного дня, конкурс одного вуза или города, разовый воркшоп — false.
- same_as: если сюжет — та же самая возможность (тот же хакатон, та же программа того же года), что одна из «Уже в радаре» под ним, — её номер, например "S41"; если та же, что сюжет этой пачки с меньшим id, — этот id; иначе null.

Ответь только JSON-массивом, по объекту на сюжет, с тем же id:
[{{"id": 1, "is_opportunity": true, "reason": null, "opp_type": "hackathon", "title_ru": "...", "organizer": "...", "summary_ru": "...", "deadline_text": "...", "deadline": "2026-11-01", "deadline_tz": null, "deadline_kind": "exact", "closed": false, "starts": null, "ends": null, "format": "online", "location": null, "eligibility": "...", "ru_access": "yes", "cost": "бесплатно", "reward": null, "apply_url": "https://...", "fit": 7, "fit_reason": "...", "major": false, "same_as": null}}]

{stories}"""


def _today(settings: config.Settings) -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y")


def _zone(text) -> tuple[timezone | ZoneInfo | None, bool]:
    """A time zone named in the text (AoE, UTC+3, Europe/Moscow…), or None."""
    name = str(text or "").strip().lower()
    if not name:
        return None, False
    if name in ZONES:
        return ZONES[name], True
    if match := OFFSET.match(name):
        sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(delta if sign == "+" else -delta), True
    try:
        return ZoneInfo(str(text).strip()), True
    except (ValueError, KeyError):
        return None, False


def deadline(value, tz_text, lang: str, now: float | None = None) -> tuple[str | None, float | None, bool]:
    """(ISO with its zone, seconds since the epoch, zone assumed) of the deadline the model gave; (None, None, False)
    for none or one that does not parse. Without a time it is the end of that day; without a zone, Moscow's for a
    Russian source and UTC for an international one."""
    text = str(value or "").strip()
    if not text or text.lower() in ("null", "none"):
        return None, None, False
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, None, False
    zone, named = _zone(tz_text)
    assumed = moment.tzinfo is None and not named
    if moment.tzinfo is None:
        if len(text) <= 10:  # a date alone: the whole day counts
            moment = moment.replace(hour=23, minute=59)
        moment = moment.replace(tzinfo=zone or (MOSCOW if lang == "ru" else UTC))
    year = datetime.fromtimestamp(now or time.time()).year
    if not year - 1 <= moment.year <= year + 3:  # a slip of the model, not a deadline
        return None, None, False
    return moment.isoformat(timespec="minutes"), moment.timestamp(), assumed


def _day(value) -> str | None:
    """An event's date as ISO, or None."""
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10]).isoformat() if text else None
    except ValueError:
        return None


def _text(value, limit: int) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] if text and text.lower() not in ("null", "none") else None


def _url(value) -> str | None:
    text = str(value or "").strip()
    return clean_url(text) if text.startswith(("http://", "https://")) else None


# --- 1. Roundups -------------------------------------------------------------------------------------------------

def _roundups(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT i.*, s.name AS source FROM items i JOIN sources s ON s.id = i.source_id WHERE i.lane = 'opps' "
        "AND i.digest = 1 AND i.backfill = 0 AND i.split_at IS NULL AND i.text IS NOT NULL AND i.published > ? "
        "ORDER BY i.published DESC LIMIT ?", (time.time() - items.OPPS_FRESH_DAYS * 86400, MAX_SPLITS_PER_RUN))]


def _roundup_block(post: dict, number: int) -> str:
    links = ", ".join(f"https://{k}" for k in json.loads(post["links"] or "[]")[:30])
    return (f"[post {number}] Источник: {post['source']}; адрес: {post['url']}\n{post['title']}\n{post['text']}"
            + (f"\nссылки: {links}" if links else ""))


async def split(settings: config.Settings) -> int:
    """Cut the waiting roundups into one post per opportunity; how many posts they gave."""
    with db.connect() as conn:
        waiting = _roundups(conn)
    made = 0
    while waiting:
        batch, size = [], 0
        while waiting and (not batch or size + len(waiting[0]["text"]) <= SPLIT_CHARS):
            size += len(waiting[0]["text"])
            batch.append(waiting.pop(0))
        if not scoring.within_budget(settings, "opps"):
            break
        listing = "\n\n".join(_roundup_block(post, n + 1) for n, post in enumerate(batch))
        answers = {a.get("post"): a for a in await scoring._ask(
            settings, SPLIT_PROMPT.format(today=_today(settings), posts=listing), lane="opps", max_tokens=8000)
            if isinstance(a, dict)}
        with db.transaction() as conn:
            for n, post in enumerate(batch):
                for k, item in enumerate((answers.get(n + 1) or {}).get("items") or [], 1):
                    if not isinstance(item, dict) or not _text(item.get("title"), 300):
                        continue
                    url = _url(item.get("url")) or post["url"]
                    text = _text(item.get("text"), 2000) or _text(item.get("title"), 300)
                    made += conn.execute(
                        "INSERT OR IGNORE INTO items (source_id, external_id, url, title, text, links, published, seen, lang, "
                        "backfill, digest, lane, copied_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'opps', ?)",
                        (post["source_id"], f"{post['external_id']}#{k}", url, _text(item.get("title"), 300), text,
                         json.dumps([link_key(url)] if url != post["url"] else []), post["published"], time.time(),
                         post["lang"], post["id"])).rowcount
                # Read once, whatever came back: the model leaves out a roundup with nothing in it instead of
                # answering an empty list (19 of 30 on 25.10.2026), and asking again would pay for the same nothing.
                conn.execute("UPDATE items SET split_at = ? WHERE id = ?", (time.time(), post["id"]))
    return made


# --- 2. Stories ----------------------------------------------------------------------------------------------------

def _waiting(conn) -> list[dict]:
    """Stories of the lane never read, or grown since they were (`grown`), and opportunities still ahead that were read
    before `major` existed."""
    return [dict(row) for row in conn.execute(
        "SELECT s.*, o.status AS opp_status, o.deadline_at AS old_deadline_at, o.deadline AS old_deadline, "
        "(o.story_id IS NULL OR s.last_updated > o.checked_at) AS grown "
        "FROM stories s LEFT JOIN opportunities o ON o.story_id = s.id "
        "WHERE s.lane = 'opps' AND s.status != 'merged' AND (o.story_id IS NULL OR s.last_updated > o.checked_at "
        "OR (o.major IS NULL AND o.status IN ('open', 'tracked', 'applied'))) "
        "ORDER BY s.last_updated DESC LIMIT ?", (MAX_PER_RUN,))]


def _posts(conn, story_id: int) -> list[dict]:
    """Up to POSTS_PER_STORY posts, one per owner, the newest first: a new post may carry the change."""
    rows = conn.execute(
        "SELECT i.title, i.text, i.url, i.links, i.lang, s.name, s.owner, s.kind FROM items i "
        "JOIN sources s ON s.id = i.source_id WHERE i.story_id = ? ORDER BY i.published DESC", (story_id,)).fetchall()
    chosen, owners = [], set()
    for row in rows:
        if row["owner"] not in owners:
            chosen.append(dict(row))
            owners.add(row["owner"])
        if len(chosen) == POSTS_PER_STORY:
            break
    return chosen


def _signal(posts: list[dict]) -> bool:
    """Whether a story may be an announcement: a source of opportunities, or a word of one in its posts."""
    return any(p["kind"] in ALWAYS_READ or SIGNAL.search(f"{p['title']} {p['text'] or ''}") for p in posts)


def _lang(posts: list[dict]) -> str:
    langs = [p["lang"] for p in posts]
    return "ru" if langs.count("ru") >= max(1, len(langs) / 2) else "en"


def describe(posts: list[dict], number: int, refs: list[dict] | None = None) -> str:
    """A story for the extraction prompt: its posts, their addresses, and the opportunities it may repeat."""
    lines = [f"[id {number}] постов: {len(posts)}, язык: {_lang(posts)}"]
    links: list[str] = []
    for post in posts:
        text = (post["text"] or "")[:POST_CHARS.get(post["kind"], OTHER_CHARS)]
        lines.append(f"- {post['name']} ({post['url']}): {post['title']}\n{text}")
        for link in [post["url"]] + [f"https://{k}" for k in json.loads(post["links"] or "[]")]:
            if link and link not in links:
                links.append(link)
    lines.append("адреса: " + ", ".join(links[:10]))
    if refs:
        lines.append("Уже в радаре: " + "; ".join(
            f"[S{r['story_id']}] {r['title_ru']} — {r['organizer'] or '?'} — дедлайн {r['deadline'] or 'не указан'}" for r in refs))
    return "\n".join(lines)


def _similar(conn, batch: list[dict]) -> dict[int, list[dict]]:
    """Up to SIMILAR_REFS read opportunities closest to each story of the batch, for `same_as`."""
    known = [dict(row) for row in conn.execute(
        "SELECT o.story_id, o.title_ru, o.organizer, o.deadline FROM opportunities o JOIN stories s ON s.id = o.story_id "
        "WHERE s.status != 'merged' AND o.status IN ('open', 'tracked', 'applied', 'hidden') AND o.title_ru IS NOT NULL")]
    batch_ids = {s["id"] for s in batch}
    known = [k for k in known if k["story_id"] not in batch_ids]
    vectors = scoring._centroids(conn, [k["story_id"] for k in known] + list(batch_ids))
    refs = {}
    for story in batch:
        if story["id"] not in vectors:
            continue
        ranked = sorted(((float(vectors[k["story_id"]] @ vectors[story["id"]]), k) for k in known if k["story_id"] in vectors),
                        key=lambda pair: -pair[0])
        refs[story["id"]] = [k for score, k in ranked[:SIMILAR_REFS] if score >= SIMILAR_FLOOR]
    return refs


async def read(settings: config.Settings, blocks: str, today: str, profile: str) -> dict:
    """One extraction call on stories described by describe(): the model's answers by story number. eval/opps calls
    it too, so the measured prompt is the one that runs."""
    return {a.get("id"): a for a in await scoring._ask(
        settings, EXTRACT_PROMPT.format(today=today, profile=profile, stories=blocks), lane="opps") if isinstance(a, dict)}


async def extract(settings: config.Settings) -> dict:
    """Read the waiting stories; a summary of what they turned out to be."""
    counts = {"read": 0, "open": 0, "expired": 0, "dropped": 0, "merged": 0}
    profile = config.clean_profile(settings.opps_profile)
    zone = ZoneInfo(settings.timezone)
    with db.connect() as conn:
        waiting = _waiting(conn)
    for start in range(0, len(waiting), EXTRACT_BATCH):
        if not scoring.within_budget(settings, "opps"):
            break
        batch = waiting[start:start + EXTRACT_BATCH]
        with db.connect() as conn:
            posts = {s["id"]: _posts(conn, s["id"]) for s in batch}
        quiet = [s for s in batch if posts[s["id"]] and not _signal(posts[s["id"]])]
        if quiet:  # no sign of an announcement: not worth a call
            with db.transaction() as conn:
                for story in quiet:
                    apply(conn, story, {"is_opportunity": False, "reason": "нет признаков анонса"}, _lang(posts[story["id"]]),
                          today=_local_day(time.time(), settings))
            counts["read"] += len(quiet)
            counts["dropped"] += len(quiet)
        batch = [s for s in batch if posts[s["id"]] and s not in quiet]
        if not batch:
            continue
        with db.connect() as conn:
            refs = _similar(conn, batch)
        blocks = "\n\n".join(describe(posts[s["id"]], n + 1, refs.get(s["id"])) for n, s in enumerate(batch))
        answers = await read(settings, blocks, _today(settings), profile)
        moved = []
        with db.transaction() as conn:
            targets: dict[int, int] = {}
            for n, story in enumerate(batch):
                answer = answers.get(n + 1)
                if not answer:
                    continue
                target = scoring._same_as(answer.get("same_as"), [{"id": r["story_id"]} for r in refs.get(story["id"], [])],
                                          targets, n + 1)
                if target is not None:
                    scoring.merge_into(conn, story["id"], target)
                    conn.execute("DELETE FROM opportunities WHERE story_id = ?", (story["id"],))
                    counts["merged"] += 1
                    continue
                status, change = apply(conn, story, answer, _lang(posts[story["id"]]), today=_local_day(time.time(), settings))
                targets[n + 1] = story["id"]
                counts["read"] += 1
                counts[status if status in counts else "open"] += 1
                if change and story["grown"]:  # a story read again only for `major`: the same posts, no news
                    moved.append(change)
            for change in moved:
                _deadline_moved(conn, zone, *change)
    return counts


def apply(conn, story: dict, answer: dict, lang: str, now: float | None = None, today: str | None = None) -> tuple[str, tuple | None]:
    """Store what the model read in a story; (its status, (story, old deadline, new deadline) when a tracked
    opportunity's deadline moved). `today` is the ISO date in the settings' time zone: passed in, since loading the
    settings inside a write would wait on the write itself."""
    now = now or time.time()
    today = today or datetime.fromtimestamp(now, MOSCOW).date().isoformat()
    kept = story.get("opp_status") if story.get("opp_status") in USER_STATUSES else None
    if not answer.get("is_opportunity"):
        reason = _text(answer.get("reason"), 120) or "не анонс"
        conn.execute(
            "INSERT INTO opportunities (story_id, status, drop_reason, checked_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (story_id) DO UPDATE SET status = excluded.status, drop_reason = excluded.drop_reason, "
            "checked_at = excluded.checked_at", (story["id"], kept or "dropped", f"не возможность: {reason}", now))
        return kept or "dropped", None
    iso, at, assumed = deadline(answer.get("deadline"), answer.get("deadline_tz"), lang, now)
    kind = answer.get("deadline_kind") if answer.get("deadline_kind") in KINDS else "exact" if at else "unknown"
    if kind == "rolling":
        iso = at = None
    status, drop = kept or "open", None
    starts = _day(answer.get("starts"))
    started = at is None and kind != "rolling" and starts is not None and starts < today
    if at is not None and at < now and kept != "applied":
        status, drop = ("expired", "дедлайн прошёл") if kept != "hidden" else ("hidden", None)
    elif answer.get("closed") is True and kept is None:  # «applications are closed», with no date given
        status, drop = "expired", "приём закрыт"
    elif started and kept is None:  # no deadline, and the event has begun: too late to apply
        status, drop = "expired", "событие уже началось"
    opp_type = answer.get("opp_type") if answer.get("opp_type") in config.OPP_TYPES else "other"
    try:
        fit = max(0, min(10, int(answer.get("fit") or 0)))
    except (TypeError, ValueError):
        fit = 0
    conn.execute(
        "INSERT INTO opportunities (story_id, opp_type, title_ru, organizer, summary_ru, deadline, deadline_at, deadline_kind, "
        "deadline_text, tz_assumed, starts, ends, format, location, eligibility, ru_access, cost, reward, apply_url, fit, "
        "fit_reason, major, status, drop_reason, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (story_id) DO UPDATE SET opp_type = excluded.opp_type, title_ru = excluded.title_ru, "
        "organizer = excluded.organizer, summary_ru = excluded.summary_ru, deadline = excluded.deadline, "
        "deadline_at = excluded.deadline_at, deadline_kind = excluded.deadline_kind, deadline_text = excluded.deadline_text, "
        "tz_assumed = excluded.tz_assumed, starts = excluded.starts, ends = excluded.ends, format = excluded.format, "
        "location = excluded.location, eligibility = excluded.eligibility, ru_access = excluded.ru_access, cost = excluded.cost, "
        "reward = excluded.reward, apply_url = excluded.apply_url, fit = excluded.fit, fit_reason = excluded.fit_reason, "
        "major = excluded.major, status = excluded.status, drop_reason = excluded.drop_reason, checked_at = excluded.checked_at",
        (story["id"], opp_type, _text(answer.get("title_ru"), 200) or story.get("title"), _text(answer.get("organizer"), 200),
         _text(answer.get("summary_ru"), 600), iso, at, kind, _text(answer.get("deadline_text"), 200), int(assumed),
         starts, _day(answer.get("ends")),
         answer.get("format") if answer.get("format") in FORMATS else None, _text(answer.get("location"), 200),
         _text(answer.get("eligibility"), 300), answer.get("ru_access") if answer.get("ru_access") in ACCESS else "unclear",
         _text(answer.get("cost"), 120), _text(answer.get("reward"), 300), _url(answer.get("apply_url")), fit,
         _text(answer.get("fit_reason"), 200), int(answer.get("major") is True), status, drop, now))
    old = story.get("old_deadline_at")
    if story.get("opp_status") == "tracked" and old and at and abs(at - old) > 86400:
        return status, (story["id"], story.get("old_deadline"), iso)
    return status, None


def _deadline_moved(conn, zone: ZoneInfo, story_id: int, old: str | None, new: str | None) -> None:
    """«🗓 Дедлайн перенесён: 15 ноя → 29 ноя» with the opportunity's card, for the bot."""
    from my_browser.news import opps_delivery  # it imports nothing of this module: no cycle at load

    rows = opps_delivery._rows(conn, "o.story_id = ?", [story_id])
    if not rows:
        return
    moved = f"{opps_delivery.day(old, zone) if old else '?'} → {opps_delivery.day(new, zone) if new else '?'}"
    text = f"🗓 <b>Дедлайн перенесён</b>: {moved}\n\n" + opps_delivery.card(rows[0], time.time(), zone)
    buttons = [[["✅ Заявка подана", f"op:{story_id}:a"], ["↩️ Не отслеживать", f"op:{story_id}:o"]]]
    conn.execute("INSERT INTO deliveries (kind, created, text, buttons, story_ids) VALUES ('opp_deadline', ?, ?, ?, ?)",
                 (time.time(), text, json.dumps(buttons, ensure_ascii=False), json.dumps([story_id])))


def _local_day(now: float, settings: config.Settings) -> str:
    """Today's date (ISO) in the settings' time zone."""
    return datetime.fromtimestamp(now, ZoneInfo(settings.timezone)).date().isoformat()


def expire(now: float | None = None) -> int:
    """Open and tracked opportunities whose deadline has passed, or without one whose event has begun, become `expired`."""
    now = now or time.time()
    today = _local_day(now, config.load())  # before the write: loading may itself write
    with db.transaction() as conn:
        passed = conn.execute(
            "UPDATE opportunities SET status = 'expired', drop_reason = 'дедлайн прошёл' WHERE status IN ('open', 'tracked') "
            "AND deadline_at IS NOT NULL AND deadline_at < ? AND deadline_kind != 'rolling'", (now,)).rowcount
        begun = conn.execute(
            "UPDATE opportunities SET status = 'expired', drop_reason = 'событие уже началось' WHERE status = 'open' "
            "AND deadline_at IS NULL AND deadline_kind != 'rolling' AND starts IS NOT NULL AND starts < ?",
            (today,)).rowcount
    return passed + begun


async def run() -> dict:
    """Cut the roundups, read the waiting stories, expire what passed; within the lane's budget."""
    settings = config.load()
    if not settings.opps_enabled:
        return {"enabled": False}
    expired = expire()
    if not scoring.within_budget(settings, "opps"):
        return {"budget": True, "expired": expired}
    made = await split(settings)
    counts = await extract(settings)
    if made or counts["read"] or counts["merged"] or expired:
        logger.info("Opportunities: %d posts cut from roundups; %d stories read: %d open, %d expired, %d not opportunities, "
                    "%d merged into earlier ones; %d expired since", made, counts["read"], counts["open"], counts["expired"],
                    counts["dropped"], counts["merged"], expired)
    return {"split": made, **counts, "expired_now": expired}


def listing(status: str | None = None, opp_type: str | None = None, limit: int = 50) -> list[dict]:
    """Opportunities with their story, the tracked first, then by deadline (none last), then by fit."""
    where, params = ["s.status != 'merged'"], []
    if status:
        where.append("o.status IN (" + ",".join("?" * len(status.split(","))) + ")")
        params += status.split(",")
    else:
        where.append("o.status IN ('open', 'tracked')")
    if opp_type:
        where.append("o.opp_type = ?")
        params.append(opp_type)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT o.*, s.title, s.source_count, s.first_seen, s.last_updated FROM opportunities o "
            f"JOIN stories s ON s.id = o.story_id WHERE {' AND '.join(where)} "
            f"ORDER BY o.status = 'tracked' DESC, o.deadline_at IS NULL, o.deadline_at, o.fit DESC LIMIT ?", (*params, limit))
        return [dict(row) for row in rows]
