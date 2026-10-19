"""«Поиск» (/): what to dig into. The topic goes into the pill at the bottom; its «Подробно» opens a sheet
with how to dig (mode, sources, model, fact-check, «Уточнить тему»). Defaults come from «Фокус»; follow-up
links pre-fill the topic, the mode and the report the new research continues (`parent`)."""

from nicegui import ui

from my_browser.common.report import ANY_ANSWER
from my_browser.common.settings import FACTCHECK_MODES
from my_browser.common.text import rub_text
from my_browser.core import queue, service
from my_browser.core.library import Library
from my_browser.core.ui import kit, progress

TOGGLE = "no-caps rounded unelevated dense color=night text-color=paper toggle-color=bordo toggle-text-color=paper"


@ui.page("/")
def search_page(topic: str = "", mode: str = "", parent: int | None = None) -> None:
    kit.shell("DEEper", active="search")
    settings = kit.prefs()
    choice = {"mode": mode if mode in kit.MODES else settings["mode"], "sources": settings["sources"],
              "writer": settings["writer"], "factcheck": settings["factcheck"]}  # factcheck None: as the mode has it
    origin = Library().get(parent) if parent else None
    linked = {"parent": origin.id if origin else None}

    def checked() -> bool:
        return choice["mode"] in FACTCHECK_MODES if choice["factcheck"] is None else choice["factcheck"]

    def start() -> None:
        query = (field.value or "").strip()
        if not query:
            kit.toast("Напишите тему исследования", "warn")
            return
        job = service.submit(query, choice["mode"], choice["sources"], choice["writer"],
                             parent_id=linked["parent"], factcheck=checked())
        ui.run_javascript("window.Notification && Notification.permission === 'default' && Notification.requestPermission()")
        ui.navigate.to(f"/job/{job['id']}")

    def options() -> None:
        dialog, body = kit.sheet("Как копаем")
        state = {"setting": False}  # the switch follows the mode until the user flips it

        def mode_changed(e) -> None:
            choice["mode"] = e.value
            mode_label.set_text(kit.MODES[e.value])
            mode_hint.set_text(kit.MODE_HINTS[e.value])
            if choice["factcheck"] is None:
                state["setting"] = True
                check.value = checked()
                state["setting"] = False

        def check_flipped(e) -> None:
            if not state["setting"]:
                choice["factcheck"] = e.value

        def writer_changed(e) -> None:
            choice["writer"] = e.value
            writer_hint.set_text(kit.WRITER_HINTS[e.value])

        async def to_clarify() -> None:
            dialog.close()
            await clarify()

        with body:
            ui.label("Режим").classes("sheet-label")
            ui.toggle(kit.MODES, value=choice["mode"], on_change=mode_changed).props(TOGGLE).classes("seg")
            mode_hint = ui.label(kit.MODE_HINTS[choice["mode"]]).classes("mono-label")
            ui.label("Источники").classes("sheet-label")
            ui.toggle(kit.SOURCE_LABELS, value=choice["sources"], on_change=lambda e: choice.update(sources=e.value)) \
                .props(TOGGLE).classes("seg")
            ui.label("Модель").classes("sheet-label")
            ui.toggle(kit.WRITER_LABELS, value=choice["writer"], on_change=writer_changed).props(TOGGLE).classes("seg")
            writer_hint = ui.label(kit.WRITER_HINTS[choice["writer"]]).classes("mono-label")
            with ui.element("div").classes("li"):
                with ui.element("div").classes("li-text"):
                    ui.label("Проверка фактов").classes("li-title")
                    ui.label("сверить утверждения с источниками · +1–2 мин").classes("li-sub")
                check = ui.switch(value=checked(), on_change=check_flipped) \
                    .props('dense color=bordo keep-color aria-label="Проверка фактов"')
            kit.secondary("Уточнить тему", to_clarify, icon="tune")
        dialog.open()

    async def clarify() -> None:
        query = (field.value or "").strip()
        if not query:
            kit.toast("Напишите тему, и я задам пару вопросов", "warn")
            return
        waiting = ui.notification("Думаю над вопросами…", position="top", spinner=True, timeout=None)
        try:
            result = await service.clarify(query)
        except Exception as e:
            kit.toast(f"Не получилось: {e}", "error")
            return
        finally:
            waiting.dismiss()
        if not result["questions"]:
            kit.toast("Уточнять нечего: тема и так конкретная")
            return
        dialog, body = kit.sheet("Уточнение")
        answers = []

        async def apply() -> None:
            chosen = [(question, toggle.value) for question, toggle in answers if toggle.value != ANY_ANSWER]
            if chosen:
                apply_button.props("loading")
                try:
                    refined = await service.refine(query, chosen)
                except Exception as e:
                    kit.toast(f"Не получилось: {e}", "error")
                    return
                finally:
                    apply_button.props(remove="loading")
                field.value = refined["topic"]
                kit.toast(f"Тема уточнена · {rub_text(result['cost_rub'] + refined['cost_rub'])}", "ok")
            dialog.close()

        with body:
            for item in result["questions"]:
                ui.label(item["q"]).classes("question")
                toggle = ui.toggle([*item["options"], ANY_ANSWER], value=ANY_ANSWER).props(TOGGLE).classes("seg wrap")
                answers.append((item["q"], toggle))
            ui.label(f"вопросы: {rub_text(result['cost_rub'])}").classes("mono-label")
            apply_button = kit.primary("Применить", apply)
        dialog.open()

    ui.element("div").classes("home-glow").props('aria-hidden="true"')
    with kit.screen("with-pill", "home"):
        with ui.element("div").classes("home-top"):
            with ui.element("div").classes("logo-row"):
                kit.graffiti("crown", "logo-crown")
                ui.html('DEE<span class="amber">per</span>', sanitize=False).classes("logo")
            with ui.link(target="/focus").classes("avatar").props('aria-label="Фокус и профиль"'):
                ui.label((settings.get("name") or "Я")[:1].upper())
        with ui.element("div").classes("home-hero"):
            with ui.element("div").classes("dip-row"):
                kit.mascot("hello", 110)
                kit.marker("DIG DEEPER?", "dip-marker")
            ui.label("Что копаем?").classes("h1 home")

            @ui.refreshable
            def going() -> None:
                running = queue.active()
                if running:
                    job = next((j for j in running if j["status"] == "running"), running[0])
                    value = progress.percent(job)
                    with ui.link(target=f"/job/{job['id']}").classes("continue"):
                        ui.label("Идёт:")
                        ui.label(job["query"]).classes("continue-topic")
                        ui.label((f"· {value}%" if value is not None else "") + " →").classes("amber")
                elif latest := Library().recent(1):
                    with ui.link(target=f"/report/{latest[0].id}").classes("continue"):
                        ui.label("Продолжить:")
                        ui.label(latest[0].query).classes("continue-topic")
                        ui.label("→")

            going()
            ui.timer(3.0, going.refresh)
            if origin:
                with ui.element("div").classes("continue linked") as link_row:
                    ui.icon("subdirectory_arrow_right")
                    ui.link(f"продолжение отчёта #{origin.id}: {origin.query}", f"/report/{origin.id}").classes("continue-topic")
                    kit.icon_button("close", "Не связывать с отчётом",
                                    lambda: (linked.update(parent=None), link_row.set_visibility(False)))

    with ui.element("div").classes("pill").props('role="search"'):
        field = ui.textarea(value=topic, placeholder="Тема или вопрос") \
            .props('borderless dense dark autogrow rows=1 enterkeyhint=send aria-label="Тема или вопрос"').classes("pill-input")
        field.on("keydown.enter.exact.prevent", start)
        field.on("keydown.meta.enter", start)
        field.on("keydown.ctrl.enter", start)
        with ui.element("button").classes("pill-opt").props('type="button" aria-label="Как копаем: режим, источники, модель"') as opt:
            mode_label = ui.label(kit.MODES[choice["mode"]])
        opt.on("click", options)
        kit.icon_button("arrow_upward", "Исследовать", start).classes("send")
