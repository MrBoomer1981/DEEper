"""The tab bar's four sections, «Поиск», «Радар», «Библиотека» and «Фокус», as one page.

All four are built at once, and app.js switches between them in the browser: at once and with an animation,
keeping the address (/, /radar, /library, /focus), «Назад» and each section's scroll position. A page per
section cost a round trip to the server and a whole new page at every tap, 0.6–0.9 s from Russia through the
proxy, and the transitions between pages, where the browser has them at all, only began after it.

Opening an address of a section (a link, a refresh, the installed app's start) builds the whole page with that
section shown. When a section is shown again, the server redraws what may have changed since (tab_shown): new
reports and researches in «Библиотека», new news and topics in «Радар». «Поиск» follows the defaults «Фокус» changes.
The page is async: it fetches the news part's stories first, so «Радар» has them at the first paint.
"""

import asyncio
import inspect

from nicegui import ui

from my_browser.core.ui import focus, kit, library, radar, search

SECTIONS = ("search", "radar", "library", "focus")
NEWS_WAIT_SEC = 1.5  # NiceGUI gives an async page 3 s; without news by then «Радар» gets them when shown


async def page(active: str, *, topic: str = "", mode: str = "", parent: int | None = None, tab: str = "reports", q: str = "") -> None:
    try:
        stories = await asyncio.wait_for(radar.news(), NEWS_WAIT_SEC)
    except asyncio.TimeoutError:
        stories = []
    kit.shell(kit.TAB_TITLES[active], active=active)
    builders = {
        "search": lambda: search.search_screen(topic, mode, parent),
        "radar": lambda: radar.radar_screen(stories),
        "library": lambda: library.library_screen(tab, q),
        "focus": focus.focus_screen,
    }
    on_show = {}
    for key in SECTIONS:
        with ui.element("section").classes("tab-screen" + (" on" if key == active else "")).props(f'data-tab="{key}"'):
            on_show[key] = builders[key]()

    async def shown(e) -> None:
        if callback := on_show.get(e.args.get("tab")):
            result = callback()
            if inspect.isawaitable(result):
                await result

    ui.on("tab_shown", shown)


@ui.page("/")
async def search_page(topic: str = "", mode: str = "", parent: int | None = None) -> None:
    await page("search", topic=topic, mode=mode, parent=parent)


@ui.page("/radar")
async def radar_page() -> None:
    await page("radar")


@ui.page("/library")
async def library_page(tab: str = "reports", q: str = "") -> None:
    await page("library", tab=tab, q=q)


@ui.page("/focus")
async def focus_page() -> None:
    await page("focus")
