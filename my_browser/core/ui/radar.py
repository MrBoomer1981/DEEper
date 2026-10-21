"""«Радар» (/radar, a section of the tab bar's page, tabs.py): what to dig into next. News and topic watching do not exist yet (plan stages «Новости» and
4), so for now the feed is the «Что исследовать дальше» topics of recent reports, minus the ones already
researched as their continuations: the freshest one as the card, the rest as rows."""

import json

from nicegui import ui

from my_browser.common.report import KINDS
from my_browser.core.library import Library
from my_browser.core.ui import kit


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


def radar_screen():
    """The section «Радар» of the tab bar's page (tabs.py); returns what to call when it is shown again."""
    shown: list[str] = []

    @ui.refreshable
    def topics_view() -> None:
        topics = feed()
        shown[:] = [t["query"] for t in topics]
        if not topics:
            with ui.element("div").classes("empty"):
                kit.mascot("sleep", 120)
                ui.label("Пока пусто. Когда появятся отчёты, я подскажу, что копать дальше. "
                         "Новости и слежка за темами — скоро.").classes("sub-text")
            return
        top, rest = topics[0], topics[1:]
        with ui.element("div").classes("story-card"):
            ui.label(f"из отчёта #{top['report'].id} · {KINDS[top['kind']].lower()}").classes("story-label")
            ui.label(top["query"]).classes("story-title")
            if top["why"]:
                ui.label(top["why"]).classes("story-why")
            kit.primary("Копнуть глубже", lambda: ui.navigate.to(kit.follow_up_url(top["query"], top["mode"], top["report"].id)))
        if rest:
            ui.label("Ещё темы из твоих отчётов").classes("sheet-label")
            with ui.element("div").classes("list"):
                for t in rest:
                    kit.row(t["query"], f"{KINDS[t['kind']].lower()} · отчёт #{t['report'].id} · {kit.day(t['report'].created)}",
                            target=kit.follow_up_url(t["query"], t["mode"], t["report"].id), lines=3)
        ui.label("Новости и слежка за темами появятся здесь позже.").classes("mono-label made-by")

    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Радар").classes("h1")
            kit.graffiti("cross", "title-cross")
        topics_view()

    def on_show() -> None:  # new reports bring new topics; redrawn only when they did, so the list does not blink
        if [t["query"] for t in feed()] != shown:
            topics_view.refresh()

    return on_show
