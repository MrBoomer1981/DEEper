"""What every screen shares: navigation, bottom sheets, list rows, the mascot, the graffiti layer and the
user's own settings (kept per browser in app.storage.user). Styles are in static/app.css.

Brand (AVEN, see DESIGN_HANDOFF.md): night #14100E background, paper #F2EBE1 text, bordo #7A2A35 only as a
fill, one amber #E8A24A action per screen; white only in the graffiti layer, which is decoration
(aria-hidden, no pointer events) and can be switched off in «Фокус».
"""

import functools
import html
import json
import os
import re
import weakref
from datetime import datetime
from urllib.parse import urlencode, urlparse

import markdown2
from nicegui import app, ui

from my_browser.common.settings import DEFAULT_SOURCES, DEFAULT_WRITER, ROOT
from my_browser.core import jobs

# Railway sets PORT: listen on every interface and require APP_PASSWORD. Locally the app listens on
# 127.0.0.1; PUBLIC=0 keeps it there on another PORT too (a second copy, e.g. another session's check).
PUBLIC = os.environ.get("PUBLIC", "1" if "PORT" in os.environ else "0") != "0"
PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH = PUBLIC or bool(PASSWORD)

MODES = {"quick": "Быстро", "detailed": "Подробно", "deep": "Глубоко"}
MODE_NAMES = {"quick": "быстрый", "detailed": "подробный", "deep": "глубокий"}
MODE_HINTS = {
    "quick": "≈ 1–4 мин · один проход",
    "detailed": "≈ 5 мин с Flash, ≈ 5–7 мин с Pro · до 5000 слов: введение, разделы по подтемам, заключение",
    "deep": "дольше всех · рекурсивный поиск вширь и вглубь, затем один отчёт",
}
SOURCE_LABELS = {"web": "Веб", "academic": "Наука", "all": "Всё"}
WRITER_LABELS = {"pro": "Pro", "flash": "Flash"}
WRITER_HINTS = {"pro": "текст пишет DeepSeek V4 Pro — сильнее, дороже ≈ 3×", "flash": "текст пишет DeepSeek Flash — быстрее и дешевле"}
WRITER_OF_MODEL = {"deepseek-v4-pro": "pro", "deepseek-flash": "flash"}
BRAND_COLORS = dict(
    primary="#7A2A35", secondary="#F2EBE1", accent="#E8A24A", dark="#1D1916", dark_page="#14100E",
    positive="#7A2A35", negative="#7A2A35", info="#7A2A35", warning="#E8A24A",
    night="#14100E", paper="#F2EBE1", bordo="#7A2A35", amber="#E8A24A",
)
NAV = [  # (section, route, Material icon, label for screen readers)
    ("search", "/", "search", "Поиск"),
    ("radar", "/radar", "radar", "Радар"),
    ("library", "/library", "bookmark_border", "Библиотека"),
    ("focus", "/focus", "tune", "Фокус"),
]
TAB_TITLES = {"search": "DEEper", "radar": "Радар — DEEper", "library": "Библиотека — DEEper", "focus": "Фокус — DEEper"}
DEFAULT_PREFS = {
    "mode": "detailed", "sources": DEFAULT_SOURCES, "writer": DEFAULT_WRITER,
    "factcheck": None,  # None: as the mode has it (common.settings.FACTCHECK_MODES)
    "graffiti": True, "preset": "mine", "profile": "", "name": "",
}


def prefs() -> dict:
    """The user's defaults from «Фокус», per browser (app.storage.user lives on the reports volume)."""
    return {**DEFAULT_PREFS, **app.storage.user.get("prefs", {})}


# The four sections of the tab bar are one page (tabs.py): «Поиск», built once, has to follow what «Фокус» changes.
_prefs_listeners: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # client -> callbacks


def on_prefs_change(callback) -> None:
    """Call `callback` when this page changes the user's defaults (save_prefs)."""
    _prefs_listeners.setdefault(ui.context.client, []).append(callback)


def save_prefs(**values) -> None:
    app.storage.user["prefs"] = {**app.storage.user.get("prefs", {}), **values}
    for callback in _prefs_listeners.get(ui.context.client, []):
        callback()


def shell(title: str, active: str | None = None) -> None:
    """Title, brand colors and the graffiti switch for every screen; the navigation for the four sections.

    The navigation is a bar at the bottom on phones and a column of icons on the left from 1024 px.
    """
    ui.page_title(title if title.endswith("DEEper") else f"{title} — DEEper")
    ui.colors(**BRAND_COLORS)
    if not prefs()["graffiti"]:
        ui.query("body").classes("graffiti-off")
    if active is None:
        return
    ui.query("body").classes("has-nav")
    with ui.element("nav").classes("tabbar").props('aria-label="Разделы"'):
        for key, route, icon, label in NAV:
            current = ' aria-current="page"' if key == active else ""
            # data-tab and data-title let app.js switch sections without a new page (tabs.py)
            with ui.link(target=route).classes("tab" + (" active" if key == active else "")) \
                    .props(f'aria-label="{label}" data-tab="{key}" data-title="{TAB_TITLES[key]}"{current}'):
                ui.icon(icon)
                ui.element("span").classes("dot")
    watch_jobs()


def watch_jobs() -> None:
    """Say when a research started from any screen is ready, while the app is open."""
    seen = {"active": {j["id"] for j in jobs.active()}}

    def check() -> None:
        now = {j["id"] for j in jobs.active()}
        for job_id in seen["active"] - now:
            job = jobs.get(job_id)
            if job and job["status"] == "done":
                ui.notify(f"Отчёт готов: {job['query']}", type="positive", position="top", timeout=8000,
                          actions=[{"label": "Открыть", "color": "amber", ":handler": f"() => location.href = '/report/{job['report_id']}'"}])
                ui.run_javascript("if (window.Notification && Notification.permission === 'granted') "
                                  f"new Notification('DEEper: отчёт готов', {{body: {json.dumps(job['query'])}}})")
        seen["active"] = now

    ui.timer(3.0, check)


def screen(*classes: str) -> ui.element:
    """The column a screen's content goes in: page gutters, safe areas, room for the bars at the bottom."""
    return ui.element("main").classes(" ".join(("screen", *classes)))


def back(fallback: str = "/") -> ui.link:
    """«Назад»: the previous screen of this app if there is one (app.js), otherwise `fallback`."""
    with ui.link(target=fallback).classes("icon-btn js-back").props('aria-label="Назад"') as link:
        ui.icon("arrow_back_ios_new")
    return link


def icon_button(icon: str, label: str, on_click=None) -> ui.element:
    button = ui.element("button").classes("icon-btn").props(f'type="button" aria-label="{label}"')
    with button:
        ui.icon(icon)
    if on_click:
        button.on("click", on_click)
    return button


# Colors of Quasar components go through their props: Quasar's own !important color classes sit in a CSS
# cascade layer, and an !important declaration in a layer beats one outside it, so app.css cannot override them.
def primary(text: str, on_click=None, icon: str | None = None) -> ui.button:
    """The screen's one amber action."""
    return ui.button(text, icon=icon, on_click=on_click).props("unelevated no-caps color=accent text-color=night").classes("btn-primary")


def secondary(text: str, on_click=None, icon: str | None = None) -> ui.button:
    return ui.button(text, icon=icon, on_click=on_click).props("outline no-caps color=paper").classes("btn-outline")


def sheet(title: str | None = None) -> tuple[ui.dialog, ui.element]:
    """A bottom sheet: (dialog, body); put the content `with body:` and call dialog.open().

    Every opening builds a new sheet, and closed ones stay in the page until the user leaves it. Deleting a closed
    sheet is no option: when one sheet opens another («⋯» → «Источники»), deleting the first one re-renders its
    siblings, and the new sheet vanished.
    """
    dialog = ui.dialog().props("position=bottom")
    with dialog:
        body = ui.element("div").classes("sheet")
    if title:
        with body:
            ui.label(title).classes("sheet-title")
    return dialog, body


def choice_sheet(title: str, options: dict, value, on_select, hints: dict | None = None, text: str = "") -> None:
    """A bottom sheet to pick one of `options` ({value: label}); the chosen one is ticked."""
    dialog, body = sheet(title)

    def pick(key) -> None:
        dialog.close()
        on_select(key)

    with body:
        if text:
            ui.label(text).classes("sub-text")
        with ui.element("div").classes("list"):
            for key, label in options.items():
                with ui.element("div").classes("li choice" + (" on" if key == value else "")).props('role="button" tabindex="0"') as item:
                    with ui.element("div").classes("li-text"):
                        ui.label(label).classes("li-title")
                        if hints and hints.get(key):
                            ui.label(hints[key]).classes("li-sub")
                    if key == value:
                        ui.icon("check").classes("li-check")
                item.on("click", lambda key=key: pick(key))
    dialog.open()


def row(title: str, sub: str = "", *, target: str | None = None, on_click=None, value: str = "",
        chevron: bool = False, accent: bool = False, lines: int = 2, note: str = "") -> ui.element:
    """One line of a list: title, a small caption (amber when `accent`), an optional `note` in text type
    (a search snippet) and an optional value at the right."""
    if target is not None:
        element = ui.link(target=target).classes("li")
    else:
        element = ui.element("div").classes("li").props('role="button" tabindex="0"')
        if on_click:
            element.on("click", on_click)
    with element:
        with ui.element("div").classes("li-text"):
            ui.label(title).classes(f"li-title lines-{lines}")
            if sub:
                ui.label(sub).classes("li-sub" + (" accent" if accent else ""))
            if note:
                ui.label(note).classes("li-note")
        if value:
            ui.label(value).classes("li-value")
        if chevron:
            ui.icon("chevron_right").classes("li-chev")
    return element


def text_tabs(options: dict[str, str], value: str, on_change) -> ui.element:
    """Tabs as words: the active one in paper with a graffiti underline (an amber line without graffiti)."""
    with ui.element("div").classes("ttabs").props('role="tablist"') as tabs:
        for key, label in options.items():
            button = ui.element("button").classes("ttab" + (" on" if key == value else "")) \
                .props(f'type="button" role="tab" aria-selected="{"true" if key == value else "false"}"')
            with button:
                ui.label(label)
            button.on("click", lambda key=key: on_change(key))
    return tabs


@functools.lru_cache(maxsize=32)
def _markdown_html(text: str) -> str:
    return markdown2.markdown(text, extras=["fenced-code-blocks", "tables"])


def markdown(text: str) -> ui.html:
    """Markdown turned into HTML on the server and shown at once, sanitized in the browser (DOMPurify) like ui.markdown.

    ui.markdown renders an empty block and fills it only after fetching its code-highlighting stylesheet, which
    takes a round trip to the server: on a phone, everything below a report showed up at the top for a moment,
    overlapping, and then jumped down.
    """
    return ui.html(_markdown_html(text)).classes("nicegui-markdown")


@functools.cache
def _mascot_svg(state: str) -> str:
    return (ROOT / "static" / "mascot" / f"dip-{state}.svg").read_text("utf-8")


def mascot(state: str, size: int) -> ui.html:
    """Дип-Диггер (hello, dig, found, sleep), inline so that its parts can move; `size` is his width."""
    return ui.html(_mascot_svg(state), sanitize=False).classes("dip-wrap").style(f"width: {size}px")


GRAFFITI = {  # white marker strokes, drawn in a 0 0 100 100-ish box each
    "crown": '<svg viewBox="0 0 64 34"><path d="M5 30 L10 6 L22 19 L32 3 L42 19 L54 6 L59 30 Z"/></svg>',
    "wave": '<svg viewBox="0 0 120 14" preserveAspectRatio="none"><path d="M3 9 C 15 2, 25 13, 37 7 S 60 3, 72 8 S 97 12, 117 5"/></svg>',
    "loop": '<svg viewBox="0 0 120 60" preserveAspectRatio="none"><path d="M18 40 C 2 20, 40 4, 70 6 C 104 8, 120 26, 100 44 C 82 58, 30 58, 14 44 C 6 36, 16 22, 30 16"/></svg>',
    "cross": '<svg viewBox="0 0 32 32"><path d="M6 7 L26 25 M25 6 L7 26"/></svg>',
    "star": '<svg viewBox="0 0 32 32"><path d="M16 3 L19.5 12 L29 12.5 L21.5 18.5 L24 28 L16 22.5 L8 28 L10.5 18.5 L3 12.5 L12.5 12 Z"/></svg>',
}


def graffiti(kind: str, classes: str = "") -> ui.html:
    """One marker stroke (crown, wave, loop, cross, star); position it with `classes` in app.css."""
    return ui.html(GRAFFITI[kind], sanitize=False, tag="span").classes(f"graffiti g-{kind} {classes}").props('aria-hidden="true"')


def marker(text: str, classes: str = "") -> ui.html:
    """A word written with a marker: Permanent Marker for Latin, Amatic SC for Cyrillic."""
    cyrillic = " cyr" if re.search("[а-яё]", text, re.I) else ""
    return ui.html(html.escape(text), sanitize=False, tag="span") \
        .classes(f"graffiti marker{cyrillic} {classes}").props('aria-hidden="true"')


def toast(message: str, kind: str = "info") -> None:
    ui.notify(message, position="top",
              type={"info": "info", "ok": "positive", "warn": "warning", "error": "negative"}[kind])


def mmss(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def number(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def domain(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def day(created: str) -> str:
    return f"{datetime.fromisoformat(created):%d.%m}"


def follow_up_url(topic: str, mode: str | None = None, parent: int | None = None) -> str:
    """The search screen pre-filled with a topic (and the report it continues)."""
    params = {"topic": topic} | ({"mode": mode} if mode else {}) | ({"parent": parent} if parent else {})
    return "/?" + urlencode(params)
