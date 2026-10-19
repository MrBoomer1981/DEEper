"""«Фокус» (/focus): the user's search rules and the app's settings, each row opening a bottom sheet."""

from nicegui import app, ui

from my_browser.core import blocklist
from my_browser.core.ui import kit


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
    with kit.screen():
        with ui.element("div").classes("title-row"):
            ui.label("Фокус").classes("h1")
            kit.marker("MY RULES", "title-marker")

        @ui.refreshable
        def rows() -> None:
            current = kit.prefs()
            with ui.element("div").classes("list"):
                kit.row("Не брать", value=str(len(blocklist.blocked_domains())), chevron=True,
                        on_click=lambda: blocklist_sheet(rows.refresh))
                with ui.element("div").classes("li"):
                    with ui.element("div").classes("li-text"):
                        ui.label("Граффити").classes("li-title")
                        ui.label("маркерные росчерки и надписи").classes("li-sub")
                    ui.switch(value=current["graffiti"], on_change=lambda e: (kit.save_prefs(graffiti=e.value),
                              ui.query("body").classes(remove="graffiti-off") if e.value else ui.query("body").classes("graffiti-off"))) \
                        .props('dense color=bordo keep-color aria-label="Граффити"')
                if kit.AUTH:
                    kit.row("Выйти", on_click=logout)

        rows()
        ui.label("made by aven").classes("mono-label made-by")
