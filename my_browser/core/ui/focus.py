"""«Фокус» (/focus): the user's search rules and the app's settings, each row opening a bottom sheet.

The rules here are the defaults of the search screen (mode, sources, model, fact-check), kept per browser
in app.storage.user. A preset sets several of them at once; changing one by hand makes the preset «Мой».
«Свежесть» waits for date filters in search; the profile card is kept for later use in query planning.
"""

from nicegui import app, ui

from my_browser.core import blocklist
from my_browser.core.ui import kit

PRESETS = {
    "mine": ("Мой", "как настроено ниже", {}),
    "scholar": ("Учёный", "наука · подробно · Pro · проверка фактов",
                {"sources": "academic", "mode": "detailed", "writer": "pro", "factcheck": True}),
    "analyst": ("Аналитик", "всё · подробно · Pro · проверка фактов",
                {"sources": "all", "mode": "detailed", "writer": "pro", "factcheck": True}),
    "news": ("Новости", "веб · быстро · Flash, без проверки", {"sources": "web", "mode": "quick", "writer": "flash", "factcheck": False}),
}
FACTCHECK = {None: "как в режиме", True: "всегда", False: "никогда"}
FACTCHECK_HINTS = {None: "в подробном и глубоком режимах да, в быстром нет", True: "+1–2\u00a0мин и ≈\u00a01–3\u00a0₽ к отчёту", False: ""}


def blocklist_sheet(on_close=None) -> None:
    """«Не брать»: sites never used as sources; core hands the list to every research."""
    dialog, body = kit.sheet("Не брать")
    with body:
        ui.label("Соцсети, видео и магазины отсеиваются всегда, остальное оценивает модель. "
                 "Сюда — сайты, которые не должны попадать в отчёты.").classes("sub-text")

        @ui.refreshable
        def listing() -> None:
            domains = blocklist.blocked_domains()
            with ui.element("div").classes("list"):
                if not domains:
                    ui.label("Список пуст").classes("mono-label")
                for d in domains:
                    with ui.element("div").classes("li compact"):
                        ui.label(d).classes("li-title lines-1 mono")
                        kit.icon_button("close", f"Разблокировать {d}", lambda d=d: (blocklist.unblock(d), listing.refresh()))

        listing()

        def add() -> None:
            if new.value.strip():
                blocklist.block(new.value.strip())
                new.value = ""
                listing.refresh()

        with ui.element("div").classes("pill inline"):
            new = ui.input(placeholder="example.com").props("borderless dark dense").classes("pill-input mono").on("keydown.enter", add)
            kit.icon_button("add", "Добавить", add).classes("send")
    if on_close:
        dialog.on("hide", on_close)
    dialog.open()


def logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


@ui.page("/focus")
def focus_page() -> None:
    kit.shell("Фокус", active="focus")

    def set_rule(**values) -> None:
        kit.save_prefs(preset="mine", **values)
        rows.refresh()

    def set_preset(key: str) -> None:
        kit.save_prefs(preset=key, **PRESETS[key][2])
        rows.refresh()

    def set_graffiti(on: bool) -> None:
        kit.save_prefs(graffiti=on)
        ui.query("body").classes(remove="graffiti-off") if on else ui.query("body").classes("graffiti-off")

    def profile_sheet() -> None:
        current = kit.prefs()
        dialog, body = kit.sheet("Что я о тебе знаю")

        def save() -> None:
            kit.save_prefs(name=name.value.strip(), profile=", ".join(p.strip() for p in interests.value.split(",") if p.strip()))
            dialog.close()
            profile.refresh()

        with body:
            ui.label("Интересы через запятую. Пока они только здесь: учитывать их в поиске я научусь позже.").classes("sub-text")
            interests = ui.textarea(value=current["profile"], placeholder="батареи, ИИ-поиск, налоги в России") \
                .props("borderless dense dark autogrow").classes("field")
            name = ui.input(value=current["name"], placeholder="Как тебя зовут (буква на главной)") \
                .props("borderless dense dark").classes("field")
            kit.primary("Сохранить", save)
        dialog.open()

    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Фокус").classes("h1")
            kit.marker("MY RULES", "title-marker")

        @ui.refreshable
        def rows() -> None:
            p = kit.prefs()
            with ui.element("div").classes("list"):
                kit.row("Пресет", value=PRESETS.get(p["preset"], PRESETS["mine"])[0], chevron=True,
                        on_click=lambda: kit.choice_sheet("Пресет", {k: v[0] for k, v in PRESETS.items()}, p["preset"], set_preset,
                                                          hints={k: v[1] for k, v in PRESETS.items()}))
                kit.row("Режим", value=kit.MODES[p["mode"]], chevron=True,
                        on_click=lambda: kit.choice_sheet("Режим", kit.MODES, p["mode"], lambda v: set_rule(mode=v), hints=kit.MODE_HINTS))
                kit.row("Источники", value=kit.SOURCE_LABELS[p["sources"]], chevron=True,
                        on_click=lambda: kit.choice_sheet("Источники", kit.SOURCE_LABELS, p["sources"], lambda v: set_rule(sources=v),
                                                          hints={"web": "SearXNG или Tavily", "academic": "arXiv и OpenAlex",
                                                                 "all": "веб и наука вместе"}))
                kit.row("Свежесть", value="скоро").classes("disabled")
                kit.row("Язык поиска", value="EN + страна", chevron=True,
                        on_click=lambda: kit.choice_sheet("Язык поиска", {"en": "EN + страна"}, "en", lambda v: None,
                                                          text="Запросы к поиску — на английском: там больше исследований и отраслевых "
                                                               "источников. Если тема про одну страну (законы, цены, ведомства), один "
                                                               "запрос — на её языке. Отчёт — на русском."))
                kit.row("Проверка фактов", value=FACTCHECK[p["factcheck"]], chevron=True,
                        on_click=lambda: kit.choice_sheet("Проверка фактов", FACTCHECK, p["factcheck"], lambda v: set_rule(factcheck=v),
                                                          hints=FACTCHECK_HINTS))
                kit.row("Не брать", value=str(len(blocklist.blocked_domains())), chevron=True,
                        on_click=lambda: blocklist_sheet(rows.refresh))
                kit.row("Модель", value=kit.WRITER_LABELS[p["writer"]], chevron=True,
                        on_click=lambda: kit.choice_sheet("Модель", kit.WRITER_LABELS, p["writer"], lambda v: set_rule(writer=v),
                                                          hints=kit.WRITER_HINTS))
                with ui.element("div").classes("li"):
                    with ui.element("div").classes("li-text"):
                        ui.label("Граффити").classes("li-title")
                        ui.label("маркерные росчерки и надписи").classes("li-sub")
                    ui.switch(value=p["graffiti"], on_change=lambda e: set_graffiti(e.value)) \
                        .props('dense color=bordo keep-color aria-label="Граффити"')
                if kit.AUTH:
                    kit.row("Выйти", on_click=logout)

        rows()

        @ui.refreshable
        def profile() -> None:
            p = kit.prefs()
            with ui.element("div").classes("profile-card").props('role="button" tabindex="0"') as card:
                ui.label("Что я о тебе знаю").classes("profile-label")
                ui.label(p["profile"] or "Пока ничего. Расскажи, чем интересуешься — нажми сюда.").classes("profile-text")
                if p["name"]:
                    ui.label(p["name"]).classes("profile-name")
            card.on("click", profile_sheet)

        profile()
        ui.label("made by aven").classes("mono-label made-by")
