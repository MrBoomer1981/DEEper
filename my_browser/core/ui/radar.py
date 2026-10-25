"""«Радар» (/radar, a section of the tab bar's page, tabs.py): AI news, opportunities, and what to dig into next.

Three tabs, «Новости», «Возможности» and «Мои», the chosen one remembered (prefs `radar_tab`).
- News: the news part (my_browser/news) gives the scored stories of the last day and a half: the most important one
  as the card, the next as rows, each saying whether a first-hand source is among its posts and how many independent
  sources reported it. A story opens the search screen with it as the topic; the arrow opens its best source. Below
  come the «Что исследовать дальше» topics of recent reports, minus the ones already researched; the card is theirs
  only while there is no news (one amber action per screen).
- Opportunities: the open ones (hackathons, schools, internships, grants) by how soon, with a filter by type; each with
  its caption as the news part writes it (`meta`: no colored marks, the brandbook has no green or red), why it fits,
  and an arrow to where to apply. A row opens its sheet: what the news part knows of it (`details`), where to apply,
  «В мои», «Регистрация есть», «Исследовать» (the search screen with it as a research topic) and «Не моё».
- Mine: the ones the user took, here or with 📌 in the bot: «Регистрации ещё нет» (tracked), «Регистрация есть»
  (applied), and the ones whose event is behind under «Прошли». The circle before a row marks the registration in
  one tap. The statuses are the bot's: a tracked one gets its deadline reminders in Telegram, a registered one no more.
All come from the news part's API (in-process unless NEWS_URL is set): the page fetches them before it is built
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
MINE_LIMIT = 200
PAST_SHOWN = 10  # «Мои» → «Прошли»: the latest ones
TABS = {"news": "Новости", "opps": "Возможности", "mine": "Мои"}
DONE = {"tracked": "Добавлено в «Мои»", "applied": "Отмечено: регистрация есть", "open": "Убрано из «Моих»",
        "hidden": "Скрыто: больше не покажу"}


async def news() -> list[dict]:
    """The news part's feed; empty when it does not answer (run apart, at NEWS_URL, and down)."""
    try:
        return (await call("news", "GET", "/feed", params={"limit": NEWS_LIMIT}, timeout=10)).json()
    except Exception:
        return []


async def opportunities() -> list[dict]:
    """The news part's open opportunities (the user's own are in mine()); empty when it does not answer."""
    try:
        return (await call("news", "GET", "/opps", params={"status": "open", "limit": OPPS_LIMIT}, timeout=10)).json()
    except Exception:
        return []


async def mine() -> list[dict]:
    """The user's own opportunities: tracked (no registration yet) and applied; empty when the news part does not answer."""
    try:
        return (await call("news", "GET", "/opps", params={"status": "tracked,applied", "limit": MINE_LIMIT}, timeout=10)).json()
    except Exception:
        return []


async def set_status(story_id: int, status: str) -> bool:
    """The user's choice, as the bot's buttons make it (tracked, applied, open, hidden); False when it did not go through."""
    try:
        await call("news", "POST", f"/opps/{story_id}/status", json={"status": status}, timeout=10)
        return True
    except Exception:
        return False


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


def _opp_row(opp: dict, on_open, on_mark=None) -> None:
    """An opportunity in a list: a tap opens its sheet, the arrow where to apply; in «Мои» the circle before it
    marks the registration (on_mark)."""
    title = _title(opp)
    with ui.element("div").classes("li news-row"):
        if on_mark:
            registered = opp["status"] == "applied"
            with ui.element("button").classes("mark" + (" on" if registered else "")) as mark:
                ui.icon("check")
            mark.props.update({"type": "button", "aria-pressed": "true" if registered else "false",
                               "aria-label": f"Регистрация есть: {title}"})
            mark.on("click", lambda: on_mark(opp))
        with ui.element("div").classes("li-text").props('role="button" tabindex="0"') as text:
            ui.label(title).classes("li-title lines-3")
            ui.label(opp["meta"]).classes("li-sub")
            note = f"Почему тебе: {opp['fit_reason']}" if opp.get("fit_reason") else opp.get("summary_ru")
            if note:
                ui.label(note).classes("li-note")
        text.on("click", lambda: on_open(opp))
        if opp.get("apply"):
            with ui.link(target=opp["apply"], new_tab=True).classes("li-src").props('aria-label="Подача"'):
                ui.icon("open_in_new")


def _opp_sheet(opp: dict, act) -> None:
    """One opportunity: what the news part knows of it, where to apply, and the user's choices (act(opp, status))."""
    dialog, body = kit.sheet()
    title, status = _title(opp), opp["status"]

    async def choose(new_status: str) -> None:
        dialog.close()
        await act(opp, new_status)

    with body:
        ui.label(opp["meta"]).classes("li-sub")
        ui.label(title).classes("opp-title")
        if opp.get("summary_ru"):
            ui.label(opp["summary_ru"]).classes("opp-text")
        if opp.get("fit_reason"):
            ui.label(f"Почему тебе: {opp['fit_reason']}").classes("opp-why")
        if opp.get("apply"):  # a real link: a new tab opened from the server would be a blocked pop-up on a phone
            with ui.link(target=opp["apply"], new_tab=True).classes("link-btn"):
                ui.label("Страница подачи")
                ui.icon("open_in_new")
        with ui.element("div").classes("opp-actions"):  # above the facts: reachable without scrolling the sheet
            if status == "open":
                kit.secondary("В мои", lambda: choose("tracked"))
            if status == "applied":
                kit.secondary("Регистрации нет", lambda: choose("tracked"))
            else:
                kit.secondary("Регистрация есть", lambda: choose("applied"))
        with ui.element("div").classes("opp-facts"):
            for label, text in opp.get("details") or []:
                ui.label(label).classes("opp-key")
                ui.label(text).classes("opp-value")
        with ui.element("div").classes("opp-more"):
            ui.link("Исследовать", kit.follow_up_url(f"{title}: условия участия, отбор и как подготовиться", "quick")) \
                .classes("more-link")
            if status in ("tracked", "applied"):
                _text_button("Убрать из «Моих»", lambda: choose("open"))
            if status != "applied":
                _text_button("Не моё", lambda: choose("hidden"))
    dialog.open()


def _text_button(text: str, on_click) -> None:
    with ui.element("button").classes("more-link").props('type="button"') as button:
        ui.label(text)
    button.on("click", on_click)


def _opps_view(items: list[dict], kind: str, on_kind, on_open) -> None:
    if not items:
        with ui.element("div").classes("empty"):
            kit.mascot("sleep", 120)
            ui.label("Новых возможностей пока нет. Они придут в бот: сводка раз в день, горящие — сразу.").classes("sub-text")
        return
    counts: dict[str, list] = {}
    for item in items:
        counts.setdefault(item["opp_type"], [item.get("type_short") or item["type_name"], 0])[1] += 1
    options = {"": f"все {len(items)}", **{key: f"{name} {n}" for key, (name, n) in sorted(counts.items(), key=lambda kv: -kv[1][1])}}
    ui.toggle(options, value=kind if kind in options else "", on_change=lambda e: on_kind(e.value or "")) \
        .props(TOGGLE).classes("seg wrap")
    ui.label("Открытые").classes("sheet-label")
    with ui.element("div").classes("list"):
        for item in items:
            if not kind or item["opp_type"] == kind:
                _opp_row(item, on_open)


def _event_day(opp: dict) -> str:
    return opp.get("starts") or ""


def _mine_view(items: list[dict], on_open, on_mark) -> None:
    if not items:
        with ui.element("div").classes("empty"):
            kit.mascot("sleep", 120)
            ui.label("Здесь будут возможности, которые ты выберешь: открой любую во «Возможностях» и нажми «В мои» "
                     "(или «Отслеживать» в боте). Зарегистрируешься — отметь кружком.").classes("sub-text")
        return
    current = [i for i in items if not i["over"]]
    past = sorted((i for i in items if i["over"]), key=lambda i: i.get("ends") or _event_day(i), reverse=True)
    groups = (("Регистрации ещё нет", [i for i in current if i["status"] == "tracked"]),  # by deadline, as /opps gives them
              ("Регистрация есть", sorted((i for i in current if i["status"] == "applied"),
                                          key=lambda i: (not _event_day(i), _event_day(i)))),
              ("Прошли", past[:PAST_SHOWN]))
    for label, group in groups:
        if group:
            ui.label(f"{label} · {len(group)}").classes("sheet-label")
            with ui.element("div").classes("list"):
                for item in group:
                    _opp_row(item, on_open, on_mark)


def radar_screen(stories: list[dict] | None = None, opps: list[dict] | None = None, own: list[dict] | None = None):
    """The section «Радар» of the tab bar's page (tabs.py); returns what to call when it is shown again."""
    shown: dict[str, list] = {"news": [], "topics": [], "opps": [], "mine": []}
    tab = kit.prefs().get("radar_tab", "news")
    state = {"tab": tab if tab in TABS else "news", "kind": "", "opps": opps or [], "mine": own or []}

    def opps_key(items: list[dict]) -> list:
        return [(o["story_id"], o["status"], o["meta"]) for o in items]

    async def act(opp: dict, status: str) -> None:
        """A status chosen in a sheet or with a circle: the news part stores it, the open tab is drawn anew."""
        if not await set_status(opp["story_id"], status):
            kit.toast("Не получилось: радар не отвечает", "error")
            return
        kit.toast("Отметка снята" if (opp["status"], status) == ("applied", "tracked") else DONE[status])
        await on_show()

    def open_sheet(opp: dict) -> None:
        _opp_sheet(opp, act)

    async def mark(opp: dict) -> None:
        await act(opp, "tracked" if opp["status"] == "applied" else "applied")

    @ui.refreshable
    def opps_view() -> None:
        shown["opps"] = opps_key(state["opps"])
        _opps_view(state["opps"], state["kind"], pick_kind, open_sheet)

    @ui.refreshable
    def mine_view() -> None:
        shown["mine"] = opps_key(state["mine"])
        _mine_view(state["mine"], open_sheet, mark)

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
        for name, box in boxes.items():
            box.set_visibility(name == key)
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
        with ui.element("div").classes("radar-part") as mine_box:
            mine_view()
        boxes = {"news": news_box, "opps": opps_box, "mine": mine_box}
        for name, box in boxes.items():
            box.set_visibility(name == state["tab"])

    async def on_show() -> None:  # redrawn only when something changed, so the lists do not blink
        if state["tab"] in ("opps", "mine"):
            key = state["tab"]
            items = await (opportunities() if key == "opps" else mine())
            if opps_key(items) != shown[key]:
                state[key] = items
                (opps_view if key == "opps" else mine_view).refresh()
            return
        items = await news()
        if news_key(items) != shown["news"]:
            news_view.refresh(items)
            topics_view.refresh(card=not items)
        elif [t["query"] for t in feed()] != shown["topics"]:
            topics_view.refresh(card=not items)

    return on_show
