"""«Радар» (/radar, a section of the tab bar's page, tabs.py): AI news, and what to dig into next.

The news part (my_browser/news) gives the scored stories of the last day and a half: the most important one as the
card, the next as rows, each saying whether a first-hand source is among its posts and how many independent sources
reported it. A story opens the search screen with it as the topic; the arrow opens its best source. Below come the
«Что исследовать дальше» topics of recent reports, minus the ones already researched; the card is theirs only while
there is no news (one amber action per screen). The stories come from the news part's API (in-process unless NEWS_URL
is set): the page fetches them before it is built (tabs.page), so they are there at the first paint.

Until 27.10.2026 «Радар» also had the tabs «Возможности» and «Мои»: the opportunities moved to their own app, OnePath.
"""

import json

from nicegui import ui

from my_browser.common.clients import call
from my_browser.common.report import KINDS
from my_browser.core.library import Library
from my_browser.core.ui import kit

NEWS_LIMIT = 12


async def news() -> list[dict]:
    """The news part's feed; empty when it does not answer (run apart, at NEWS_URL, and down)."""
    try:
        return (await call("news", "GET", "/feed", params={"limit": NEWS_LIMIT}, timeout=10)).json()
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


def radar_screen(stories: list[dict] | None = None):
    """The section «Радар» of the tab bar's page (tabs.py); returns what to call when it is shown again."""
    shown: dict[str, list] = {"news": [], "topics": []}

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
                ui.label(f"из отчёта #{top['report'].id} · {KINDS.get(top['kind'], top['kind']).lower()}").classes("story-label")
                ui.label(top["query"]).classes("story-title")
                if top["why"]:
                    ui.label(top["why"]).classes("story-why")
                kit.primary("Копнуть глубже", lambda: kit.start_followup(top["query"], top["report"].id))
        if rest:
            ui.label("Темы из твоих отчётов").classes("sheet-label")
            with ui.element("div").classes("list"):
                for t in rest:
                    kit.row(t["query"], kit.followup_sub(f"{KINDS.get(t['kind'], t['kind']).lower()} · отчёт #{t['report'].id}"),
                            on_click=lambda t=t: kit.start_followup(t["query"], t["report"].id), lines=3)

    stories = stories or []
    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Радар").classes("h1")
            kit.graffiti("cross", "title-cross")
        news_view(stories)
        topics_view(card=not stories)

    async def on_show() -> None:  # redrawn only when something changed, so the lists do not blink
        items = await news()
        if news_key(items) != shown["news"]:
            news_view.refresh(items)
            topics_view.refresh(card=not items)
        elif [t["query"] for t in feed()] != shown["topics"]:
            topics_view.refresh(card=not items)

    return on_show
