"""«Библиотека» (/library): reports (running researches first), bookmarks, watched topics."""

from nicegui import ui

from my_browser.common.text import plural
from my_browser.core import queue
from my_browser.core.library import Library
from my_browser.core.ui import kit


@ui.page("/library")
def library_page() -> None:
    kit.shell("Библиотека", active="library")
    with kit.screen():
        ui.label("Библиотека").classes("h1")
        with ui.element("div").classes("list"):
            for job in queue.active():
                kit.row(job["query"], f"{kit.MODE_NAMES.get(job['mode'], job['mode'])} · "
                        f"{'идёт' if job['status'] == 'running' else 'в очереди'}", target=f"/job/{job['id']}", accent=True)
            for e in Library().recent(100):
                kit.row(e.query, f"{kit.day(e.created)} · {kit.MODE_NAMES.get(e.mode, e.mode)} · "
                        f"{kit.number(e.words)} {plural(e.words, 'слово', 'слова', 'слов')}", target=f"/report/{e.id}")
