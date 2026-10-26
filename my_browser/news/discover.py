"""Opportunities found by web search once a week, beyond the lane's sources.

The user's request of 26.10.2026: announcements that no source of the lane carries (the VI Congress of Young
Scientists was found by hand) are looked for on the web. Once a week (Monday at SEARCH_TIME, before the app's morning
update), and at the first run after the deploy:
1. Flash writes QUERIES search queries from the user's profile (without contacts), the types shown and the months
   ahead, in Russian and English (QUERIES_PROMPT); each annual event the radar awaits and has not found this season
   (annual.expected()) adds a query of its own, AWAITED_QUERIES at most.
2. Each query goes to the web search the research uses (common.settings.WEB_RETRIEVER: Tavily on Railway, the local
   SearXNG otherwise), RESULTS_PER_QUERY results.
3. A result whose page the lane has not seen (its link_key among the lane's posts and their links) is fetched and its
   main text read (collectors.page.parse(); the search's snippet when the page shows nothing without JavaScript), at
   most NEW_PAGES a run, and stored as a post of the source «Поиск по вебу» (kind `search`, which the loop never polls).
   From there merging and reading take it like any post: a page of an opportunity already known joins its story by
   its link, a new one is read and comes out at the next morning update.
The week is marked done whatever the search answered, so a search engine that is down costs one query-writing call a
week, not one a minute. Within the lane's budget (scoring.within_budget(settings, "opps")); `opps_search` off, or the
source switched off with /remove, stops it.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from my_browser.common.settings import WEB_RETRIEVER
from my_browser.news import annual, config, db, items, scoring, sources
from my_browser.news.collectors import Post, clean_url, host, link_key, page

logger = logging.getLogger(__name__)

SOURCE_URL = "web-search"
QUERIES = 12
AWAITED_QUERIES = 6  # one per awaited annual event, the most recently opened first: ≈ 75 of Tavily's 1 000 a month in all
RESULTS_PER_QUERY = 5
NEW_PAGES = 40
PAGE_CHARS = 4000
SEARCH_WEEKDAY, SEARCH_TIME = 0, "05:00"  # Monday, three hours before the morning update
TIMEOUT = httpx.Timeout(20, connect=10)
SKIP_HOSTS = {"t.me", "vk.com", "vk.ru", "youtube.com", "dzen.ru", "instagram.com", "facebook.com", "x.com", "twitter.com"}
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")

QUERIES_PROMPT = """Ты помогаешь студенту не пропустить возможности: хакатоны, соревнования, школы, стажировки, исследовательские программы, поступление, гранты и стипендии, конференции, форумы и конгрессы для молодых учёных. Сегодня {today}.

Профиль студента: {profile}
Что ему показывать: {types}.

Составь {n} поисковых запросов для веб-поиска, чтобы найти анонсы, куда можно подать заявку или зарегистрироваться сейчас или в ближайшие месяцы ({months}). Половина запросов — по-русски (российские возможности), половина — по-английски (международные, открытые для граждан России). Запрос — как в поисковике: что ищем, тема, год, «регистрация», «приём заявок», «deadline», «apply». Разные запросы — о разном: не повторяй один запрос другими словами.

Ответь только JSON-массивом строк."""


def source() -> dict:
    """The lane's source «Поиск по вебу», made on first use: its row keeps the runs' health like any source's."""
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM sources WHERE kind = 'search' AND url = ?", (SOURCE_URL,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sources (kind, url, name, lane, category, lang, owner, interval, added) "
                         "VALUES ('search', ?, 'Поиск по вебу', 'opps', 'aggregator', 'ru', 'web-search', 10080, ?)",
                         (SOURCE_URL, time.time()))
            row = conn.execute("SELECT * FROM sources WHERE kind = 'search' AND url = ?", (SOURCE_URL,)).fetchone()
    return dict(row)


def _due(local: datetime) -> str | None:
    """This week's key («2026-W44») when its search is due and not made yet: from Monday's SEARCH_TIME on, or at once
    when no search was ever made."""
    done = db.get_state("opps_search_done")
    week = f"{local:%G-W%V}"
    if done is None:
        return week
    if week in done:
        return None
    hours, minutes = map(int, SEARCH_TIME.split(":"))
    slot = (local - timedelta(days=(local.weekday() - SEARCH_WEEKDAY) % 7)).replace(
        hour=hours, minute=minutes, second=0, microsecond=0)
    return week if local >= slot else None


async def _queries(settings: config.Settings, local: datetime) -> list[str]:
    months = ", ".join(f"{MONTHS[(local.month - 1 + i) % 12]} {local.year + (local.month - 1 + i) // 12}" for i in range(6))
    prompt = QUERIES_PROMPT.format(today=f"{local:%d.%m.%Y}", profile=config.clean_profile(settings.opps_profile),
                                   types=", ".join(config.OPP_TYPES[t] for t in settings.opps_types), n=QUERIES,
                                   months=months)
    return [q for q in await scoring._ask(settings, prompt, lane="opps", max_tokens=1500) if isinstance(q, str)][:QUERIES]


async def search(client: httpx.AsyncClient, query: str) -> list[dict]:
    """The web search's results for a query: `url`, `title`, `snippet`."""
    if WEB_RETRIEVER == "tavily":
        response = await client.post("https://api.tavily.com/search", json={
            "api_key": os.environ.get("TAVILY_API_KEY", ""), "query": query, "max_results": RESULTS_PER_QUERY,
            "search_depth": "basic"})
        response.raise_for_status()
        found = response.json().get("results") or []
    elif WEB_RETRIEVER == "searx":
        response = await client.get(f"{os.environ.get('SEARX_URL', 'http://127.0.0.1:8080').rstrip('/')}/search",
                                    params={"q": query, "format": "json"})
        response.raise_for_status()
        found = response.json().get("results") or []
    else:
        raise ValueError(f"web search {WEB_RETRIEVER!r} is not supported here")
    return [{"url": r["url"], "title": r.get("title") or "", "snippet": r.get("content") or ""}
            for r in found[:RESULTS_PER_QUERY] if r.get("url", "").startswith(("http://", "https://"))]


def _known() -> set[str]:
    """The link keys of every page the lane has seen: its posts' own addresses and the pages they link to."""
    with db.connect() as conn:
        keys = {row[0] for row in conn.execute(
            "SELECT DISTINCT j.value FROM items i, json_each(COALESCE(i.links, '[]')) j WHERE i.lane = 'opps'")}
        keys |= {link_key(row[0]) for row in conn.execute("SELECT url FROM items WHERE lane = 'opps'")}
    return keys


async def _pages(client: httpx.AsyncClient, results: list[dict]) -> list[Post]:
    """New pages among the results, read: a post each, one a site (aij.ru gave its registration in three languages),
    at most NEW_PAGES."""
    known, sites, posts = _known(), set(), []
    for result in results:
        url = clean_url(result["url"])
        key, site = link_key(url), host(url)
        if key in known or site in SKIP_HOSTS or site in sites:
            continue
        known.add(key)
        sites.add(site)
        title, text = result["title"], result["snippet"]
        try:
            response = await client.get(url)
            response.raise_for_status()
            if "html" in response.headers.get("content-type", ""):
                page_title, page_text, _ = await asyncio.to_thread(page.parse, response.text, str(response.url))
                if len(page_text) >= page.MIN_TEXT:
                    title, text = page_title or title, page_text
        except Exception:  # the snippet stands in for a page that does not answer
            pass
        if text:
            posts.append(Post(key, url, " ".join((title or url).split())[:300], text[:PAGE_CHARS], [key], time.time()))
        if len(posts) >= NEW_PAGES:
            break
    return posts


async def run(now: float | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """The week's search when it is due; a summary of what it found. `transport` stands in for the network in checks."""
    settings = config.load()
    if not settings.opps_enabled or not settings.opps_search:
        return {"enabled": False}
    zone = ZoneInfo(settings.timezone)
    now = now or time.time()
    local = datetime.fromtimestamp(now, zone)
    week = _due(local)
    if week is None:
        return {"due": False}
    feed = source()
    if not feed["enabled"]:
        return {"enabled": False}
    if not scoring.within_budget(settings, "opps"):
        return {"budget": True}
    from my_browser.news.loop import USER_AGENT  # the loop imports this module

    awaited = [f"{event['name']} {local.year} регистрация" for event in annual.expected(now)][:AWAITED_QUERIES]
    queries = list(dict.fromkeys(await _queries(settings, local) + awaited))
    results, failed = [], 0
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True,
                                 transport=transport) as client:
        for query in queries:
            try:
                results += await search(client, query)
            except Exception as e:
                failed += 1
                logger.warning("Opportunities: web search failed for %r (%s)", query, str(e)[:200])
        posts = await _pages(client, results)
    fresh, _ = items.store(feed, posts, 0)
    if failed < len(queries):
        sources.mark_ok(feed["id"])
    else:
        sources.mark_failed(feed["id"], "поиск не ответил ни на один запрос")
    db.set_state("opps_search_done", ((db.get_state("opps_search_done") or []) + [week])[-10:])
    logger.info("Opportunities: web search, %d queries (%d for awaited events), %d failed, %d results, %d new pages",
                len(queries), len(awaited), failed, len(results), fresh)
    return {"queries": len(queries), "awaited": len(awaited), "failed": failed, "results": len(results), "new": fresh}
