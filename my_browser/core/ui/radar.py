"""«Радар» (/radar): what to dig into next. Until news and topic watching exist (plan stages «Новости» and 4)
it shows the follow-up topics of recent reports."""

from nicegui import ui

from my_browser.core.ui import kit


@ui.page("/radar")
def radar_page() -> None:
    kit.shell("Радар", active="radar")
    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Радар").classes("h1")
            kit.graffiti("cross", "title-cross")
        with ui.element("div").classes("empty"):
            kit.mascot("sleep", 120)
            ui.label("Добавь темы в Фокусе — я буду следить").classes("sub-text")
