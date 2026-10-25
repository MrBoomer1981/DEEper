"""«Радар» (/radar, a section of the tab bar's page, tabs.py): AI news, opportunities, and what to dig into next.

Two tabs, «Новости» and «Возможности», the chosen one remembered (prefs `radar_tab`).
- News: the news part (my_browser/news) gives the scored stories of the last day and a half: the most important one
  as the card, the next as rows, each saying whether a first-hand source is among its posts and how many independent
  sources reported it. A story opens the search screen with it as the topic; the arrow opens its best source. Below
  come the «Что исследовать дальше» topics of recent reports, minus the ones already researched; the card is theirs
  only while there is no news (one amber action per screen).
- Opportunities: the open and tracked ones (hackathons, schools, internships, grants), the tracked first, then by how
  soon, with a filter by type; each with its caption as the news part writes it (`meta`: no colored marks, the
  brandbook has no green or red), why it fits, and an arrow to where to apply. A row opens the search screen with the
  opportunity as a research topic. Tracking and hiding stay in the bot.
Both come from the news part's API (in-process unless NEWS_URL is set): the page fetches them before it is built
(tabs.page), so they are there at the first paint.
"""

import json

from nicegui import ui

from my_browser.common.clients import call
from my_browser.common.report import KINDS
from my_browser.core.library import Library
from my_browser.core.ui import kit
from my_browser.core.ui.search import TOGGLE

NEWS_LIMIT = 12
OPPS_LIMIT = 60
TABS = {"news": "Новости", "opps": "Возможности"}


async def news() -> list[dict]:
    """The news part's feed; empty when it does not answer (run apart, at NEWS_URL, and down)."""
    try:
        return (await call("news", "GET", "/feed", params={"limit": NEWS_LIMIT}, timeout=10)).json()
    except Exception:
        return []


async def opportunities() -> list[dict]:
    """The news part's open and tracked opportunities; empty when it does not answer."""
    try:
        return (await call("news", "GET", "/opps", params={"limit": OPPS_LIMIT}, timeout=10)).json()
    except Exception:
        return []


def feed(limit: int = 12) -> list[dict]:
    """Follow-up topics of the latest reports, newest report first, without the ones already continued."""
    library, topics, seen = Library(), [], set()
    for entry in library.recent(15):
        try:
            meta = json.loads(entry.base.with_suffix(".json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        done = {child.query.strip().lower() for child in library.children(entry)}
        for topic in meta.get("followups") or []:
            key = topic["query"].strip().lower()
            if key in seen or key in done:
                continue
            seen.add(key)
            topics.append({**topic, "report": entry})
    return topics[:limit]


def _title(story: dict) -> str:
    return story["title_ru"] or story["title"] or ""


def _news_view(stories: list[dict]) -> None:
    top, rest = stories[0], stories[1:]
    with ui.element("div").classes("story-card"):
        ui.label(top["header"]).classes("story-label")
        ui.label(_title(top)).classes("story-title")
        if top["summary_ru"]:
            ui.label(top["summary_ru"]).classes("story-why")
        with ui.element("div").classes("story-actions"):
            kit.primary("Копнуть глубже", lambda: ui.navigate.to(kit.follow_up_url(_title(top))))
            if top["link"]:
                ui.link(f"{top['link_label']} ↗", top["link"], new_tab=True).classes("plain-link story-src")
    if rest:
        ui.label("Ещё новости").classes("sheet-label")
        with ui.element("div").classes("list"):
            for story in rest:
                with ui.element("div").classes("li news-row"):
                    with ui.link(target=kit.follow_up_url(_title(story))).classes("li-text"):
                        ui.label(_title(story)).classes("li-title lines-3")
                        ui.label(story["header"]).classes("li-sub")
                        if story["summary_ru"]:
                            ui.label(story["summary_ru"]).classes("li-note")
                    if story["link"]:
                        with ui.link(target=story["link"], new_tab=True).classes("li-src") \
                                .props(f'aria-label="Источник: {story["link_label"]}"'):
                            ui.icon("open_in_new")


def _opp_row(opp: dict) -> None:
    title = opp["title_ru"] or opp["title"] or ""
    with ui.element("div").classes("li news-row"):
        with ui.link(target=kit.follow_up_url(f"{title}: условия участия, отбор и как подготовиться", "quick")).classes("li-text"):
            ui.label(title).classes("li-title lines-3")
            ui.label(opp["meta"]).classes("li-sub")
            note = f"Почему тебе: {opp['fit_reason']}" if opp.get("fit_reason") else opp.get("summary_ru")
            if note:
                ui.label(note).classes("li-note")
        if opp.get("apply"):
            with ui.link(target=opp["apply"], new_tab=True).classes("li-src").props('aria-label="Подача"'):
                ui.icon("open_in_new")


def _opps_view(items: list[dict], kind: str, on_kind) -> None:
    if not items:
        with ui.element("div").classes("empty"):
            kit.mascot("sleep", 120)
            ui.label("Возможностей пока нет: источники разбираются. Новые придут в бот — сводкой по воскресеньям, "
                     "горящие сразу.").classes("sub-text")
        return
    counts: dict[str, list] = {}
    for item in items:
        counts.setdefault(item["opp_type"], [item.get("type_short") or item["type_name"], 0])[1] += 1
    options = {"": f"все {len(items)}", **{key: f"{name} {n}" for key, (name, n) in sorted(counts.items(), key=lambda kv: -kv[1][1])}}
    ui.toggle(options, value=kind if kind in options else "", on_change=lambda e: on_kind(e.value or "")) \
        .props(TOGGLE).classes("seg wrap")
    chosen = [i for i in items if not kind or i["opp_type"] == kind]
    for label, group in (("Отслеживаю", [i for i in chosen if i["status"] == "tracked"]),
                         ("Открытые", [i for i in chosen if i["status"] != "tracked"])):
        if group:
            ui.label(label).classes("sheet-label")
            with ui.element("div").classes("list"):
                for item in group:
                    _opp_row(item)


def radar_screen(stories: list[dict] | None = None, opps: list[dict] | None = None):
    """The section «Радар» of the tab bar's page (tabs.py); returns what to call when it is shown again."""
    shown: dict[str, list] = {"news": [], "topics": [], "opps": []}
    tab = kit.prefs().get("radar_tab", "news")
    state = {"tab": tab if tab in TABS else "news", "kind": "", "opps": opps or []}

    def opps_key(items: list[dict]) -> list:
        return [(o["story_id"], o["status"], o["meta"]) for o in items]

    @ui.refreshable
    def opps_view() -> None:
        shown["opps"] = opps_key(state["opps"])
        _opps_view(state["opps"], state["kind"], pick_kind)

    def pick_kind(kind: str) -> None:
        state["kind"] = kind
        opps_view.refresh()

    @ui.refreshable
    def tabs_view() -> None:
        kit.text_tabs(TABS, state["tab"], switch)

    async def switch(key: str) -> None:
        state["tab"] = key
        kit.save_prefs(radar_tab=key)
        tabs_view.refresh()
        news_box.set_visibility(key == "news")
        opps_box.set_visibility(key == "opps")
        await on_show()

    def news_key(items: list[dict]) -> list:
        return [(s["id"], s["source_count"], s["summary_ru"]) for s in items]

    @ui.refreshable
    def news_view(items: list[dict]) -> None:
        shown["news"] = news_key(items)
        if items:
            _news_view(items)

    @ui.refreshable
    def topics_view(card: bool) -> None:
        topics = feed()
        shown["topics"] = [t["query"] for t in topics]
        if not topics:
            if card:  # neither news nor topics
                with ui.element("div").classes("empty"):
                    kit.mascot("sleep", 120)
                    ui.label("Пока пусто. Здесь будут главные новости об ИИ и темы для исследований из твоих отчётов.") \
                        .classes("sub-text")
            return
        rest = topics
        if card:
            top, rest = topics[0], topics[1:]
            with ui.element("div").classes("story-card"):
                ui.label(f"из отчёта #{top['report'].id} · {KINDS[top['kind']].lower()}").classes("story-label")
                ui.label(top["query"]).classes("story-title")
                if top["why"]:
                    ui.label(top["why"]).classes("story-why")
                kit.primary("Копнуть глубже", lambda: ui.navigate.to(kit.follow_up_url(top["query"], top["mode"], top["report"].id)))
        if rest:
            ui.label("Темы из твоих отчётов").classes("sheet-label")
            with ui.element("div").classes("list"):
                for t in rest:
                    kit.row(t["query"], f"{KINDS[t['kind']].lower()} · отчёт #{t['report'].id} · {kit.day(t['report'].created)}",
                            target=kit.follow_up_url(t["query"], t["mode"], t["report"].id), lines=3)

    stories = stories or []
    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Радар").classes("h1")
            kit.graffiti("cross", "title-cross")
        tabs_view()
        with ui.element("div").classes("radar-part") as news_box:
            news_view(stories)
            topics_view(card=not stories)
        with ui.element("div").classes("radar-part") as opps_box:
            opps_view()
        news_box.set_visibility(state["tab"] == "news")
        opps_box.set_visibility(state["tab"] == "opps")

    async def on_show() -> None:  # redrawn only when something changed, so the lists do not blink
        if state["tab"] == "opps":
            items = await opportunities()
            if opps_key(items) != shown["opps"]:
                state["opps"] = items
                opps_view.refresh()
            return
        items = await news()
        if news_key(items) != shown["news"]:
            news_view.refresh(items)
            topics_view.refresh(card=not items)
        elif [t["query"] for t in feed()] != shown["topics"]:
            topics_view.refresh(card=not items)

    return on_show
