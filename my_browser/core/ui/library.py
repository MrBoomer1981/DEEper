"""«Библиотека» (/library, a section of the tab bar's page, tabs.py): reports (running researches first),
bookmarks, watched topics.

The search icon turns the title into a search field (Library.search: every word as a prefix, so Russian
word forms match). The tab is kept in the address (?tab=) so that «Назад» from a report comes back to it.
"""

import re

from nicegui import ui

from my_browser.core import bookmarks, jobs
from my_browser.core.library import Library
from my_browser.core.ui import kit, progress

TABS = {"reports": "Отчёты", "bookmarks": "Закладки", "watching": "Слежу"}


def snapshot() -> tuple:
    """What the reports tab shows: when it changed since the section was drawn, showing it again redraws it."""
    return (tuple(e.id for e in Library().recent(200)),
            tuple((j["id"], j["status"], progress.percent(j)) for j in jobs.active()), len(bookmarks.load()))


def library_screen(tab: str = "reports", q: str = ""):
    """The section «Библиотека» of the tab bar's page (tabs.py); returns what to call when it is shown again."""
    state = {"tab": tab if tab in TABS else "reports", "q": q, "searching": bool(q), "drawn": None}

    def switch(key: str) -> None:
        state["tab"] = key
        ui.run_javascript(f"history.replaceState(null, '', '/library?tab={key}')")
        tabs.refresh()
        content.refresh()

    def search(value: str) -> None:
        state["q"] = value or ""
        content.refresh()

    def toggle_search(on: bool) -> None:
        state["searching"] = on
        if not on:
            state["q"] = ""
        head.refresh()
        tabs.refresh()
        content.refresh()

    def reports(q: str = "") -> None:
        library = Library()
        found = library.search(q, 40) if q else [(e, "") for e in library.recent(200)]
        running = [] if q else jobs.active()
        if not found and not running:
            with ui.element("div").classes("empty"):
                kit.mascot("hello", 110)
                ui.label("Ничего не нашлось." if q else "Здесь будут отчёты. Начни с вопроса на главной.").classes("sub-text")
            return
        with ui.element("div").classes("list"):
            for job in running:
                value = progress.percent(job)
                kit.row(job["query"], ("идёт" if job["status"] == "running" else "в очереди")
                        + (f" · {value}%" if value is not None else ""), target=f"/job/{job['id']}", accent=True)
            for e, snippet in found:
                kit.row(e.query, f"{kit.day(e.created)} · {kit.MODE_NAMES.get(e.mode, e.mode)} · ≈\u00a0{e.reading_min}\u00a0мин чтения",
                        target=f"/report/{e.id}", note=re.sub(r"[#*_`>|]+", "", snippet).strip())

    def saved() -> None:
        items = bookmarks.load()
        if not items:
            with ui.element("div").classes("empty"):
                kit.mascot("found", 120)
                ui.label("Выдели фразу в отчёте и нажми «Сохранить».").classes("sub-text")
            return
        library = Library()

        def remove(bookmark_id: str) -> None:
            bookmarks.delete(bookmark_id)
            content.refresh()

        with ui.element("div").classes("list"):
            for b in items:
                entry = library.by_name(b["report"]) or library.get(b["report_id"])
                caption = " · ".join(part for part in (b.get("folder"), b.get("section"), f"отчёт #{entry.id}" if entry else "отчёт удалён") if part)
                with ui.element("div").classes("li quote"):
                    target = f"/report/{entry.id}" if entry else None
                    with (ui.link(target=target) if target else ui.element("div")).classes("li-text plain"):
                        ui.label(f"«{b['text']}»").classes("quote-text")
                        ui.label(caption).classes("li-sub")
                    kit.icon_button("close", "Удалить закладку", lambda bid=b["id"]: remove(bid))

    with kit.screen():
        @ui.refreshable
        def head() -> None:
            if state["searching"]:
                with ui.element("div").classes("pill inline search-pill"):
                    field = ui.input(value=state["q"], placeholder="Поиск по отчётам", on_change=lambda e: search(e.value)) \
                        .props('borderless dense dark autofocus aria-label="Поиск по отчётам"').classes("pill-input")
                    field.props("debounce=300")
                    kit.icon_button("close", "Закрыть поиск", lambda: toggle_search(False))
            else:
                with ui.element("div").classes("title-row spread"):
                    ui.label("Библиотека").classes("h1")
                    kit.icon_button("search", "Поиск по отчётам", lambda: toggle_search(True))

        head()

        @ui.refreshable
        def tabs() -> None:
            if not state["searching"]:
                kit.text_tabs(TABS, state["tab"], switch)

        tabs()

        @ui.refreshable
        def content() -> None:
            state["drawn"] = snapshot()
            if state["searching"]:
                reports(state["q"])
            elif state["tab"] == "bookmarks":
                saved()
            elif state["tab"] == "watching":
                with ui.element("div").classes("empty"):
                    kit.mascot("sleep", 120)
                    ui.label("Здесь будут темы, за которыми я слежу: появятся вместе с наблюдением за темами.").classes("sub-text")
            else:
                reports()

        content()

    def on_show() -> None:  # a finished research or a new report since; redrawn only then, so the list does not blink
        if not state["searching"] and snapshot() != state["drawn"]:
            content.refresh()

    return on_show
