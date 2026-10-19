"""«Исследование идёт» (/job/{id}): Дип копает, the percent, the phase, the last sources read. Polls core's
queue every second. Queued: Дип sleeps and says the place in line; stopped or interrupted: the error and
«Перезапустить»; done: Дип shows his find for a moment, then the report opens."""

from fastapi.responses import RedirectResponse
from nicegui import ui

from my_browser.common.text import rub_text
from my_browser.core import queue
from my_browser.core.library import Library
from my_browser.core.ui import kit, progress


def _kind(job: dict) -> str:
    return {"queued": "queued", "running": "running", "done": "done"}.get(job["status"], "stopped")


@ui.page("/job/{job_id}")
def job_page(job_id: str) -> RedirectResponse | None:
    job = queue.get(job_id)
    if job and job["status"] == "done":
        return RedirectResponse(f"/report/{job['report_id']}")
    kit.shell("Исследование")
    with kit.screen("no-nav", "job"):
        with ui.element("div").classes("topbar"):
            kit.back("/")
        if not job:
            with ui.element("div").classes("empty"):
                kit.mascot("hello", 110)
                ui.label("Такого исследования нет. Готовые отчёты — в Библиотеке.").classes("sub-text")
                ui.link("Открыть Библиотеку", "/library").classes("plain-link")
            return None
        ui.label(job["query"]).classes("job-topic")
        state = {"kind": None, "sources": None, "job": job}

        def restart() -> None:
            queue.restart(job_id)
            ui.navigate.to(f"/job/{job_id}")

        def details() -> None:
            current = queue.get(job_id) or state["job"]
            dialog, body = kit.sheet("Подробнее")
            with body:
                facts = [kit.MODE_NAMES.get(current["mode"], current["mode"]), kit.SOURCE_LABELS.get(current["sources"], current["sources"]).lower(),
                         f"текст: {current['writer']}", f"идёт {kit.mmss(current['elapsed'])}"]
                if current["cost_rub"]:
                    facts.append(rub_text(current["cost_rub"]))
                if current["factcheck"]:
                    facts.append("проверка фактов")
                if current["parent"] and (origin := Library().by_name(current["parent"])):
                    facts.append(f"продолжение #{origin.id}")
                if current["origin"] == "telegram":
                    facts.append("из telegram")
                ui.label(" · ".join(facts)).classes("mono-label")
                if current["queries"]:
                    ui.label(f"Поисковые запросы · {len(current['queries'])}").classes("sheet-label")
                    with ui.element("div").classes("plain-list"):
                        for q in current["queries"]:
                            ui.label(q)
                if current["urls"]:
                    ui.label(f"Источники · {len(current['urls'])}").classes("sheet-label")
                    with ui.element("div").classes("list"):
                        for url, score in current["urls"]:
                            with ui.link(target=url, new_tab=True).classes("li compact source"):
                                ui.label(score or "–").classes(f"score score-{(score or '-')[0]}")
                                with ui.element("div").classes("li-text"):
                                    ui.label(kit.domain(url)).classes("li-title lines-1")
                                    ui.label(url).classes("li-sub lines-1 url")
                if current["log"]:
                    ui.label("Журнал").classes("sheet-label")
                    ui.label("\n".join(current["log"][-60:])).classes("job-log")
            dialog.open()

        @ui.refreshable
        def stage() -> None:
            current = state["job"]
            kind = _kind(current)
            with ui.element("div").classes("job-stage"):
                if kind == "running":
                    kit.mascot("dig", 140)
                    kit.marker("DIGGING…", "job-marker")
                elif kind == "queued":
                    kit.mascot("sleep", 140)
                elif kind == "done":
                    kit.mascot("found", 140)
                    kit.marker("FOUND!", "job-marker")
                else:
                    kit.mascot("hello", 140)
            if kind == "stopped":
                interrupted = current["status"] == "interrupted"
                ui.label("Исследование прервано: сервер перезапускался." if interrupted else current["error"] or "Ошибка") \
                    .classes("job-error")
                kit.primary("Перезапустить" if interrupted else "Попробовать снова", restart, icon="refresh")

        with ui.element("div").classes("job-main"):
            stage()
            with ui.element("div").classes("pct-row") as pct_row:
                pct = ui.label().classes("pct")
                ui.label("%").classes("pct-sign")
            status = ui.label().classes("job-status")
            sub = ui.label().classes("job-sub")
            with ui.element("div").classes("bar") as bar:
                fill = ui.element("div")
            recent = ui.element("div").classes("recent")
            with ui.element("button").classes("more-link").props('type="button"') as more:
                ui.label("Подробнее")
            more.on("click", details)

        with ui.element("div").classes("job-bottom"):
            kit.secondary("Свернуть", lambda: ui.navigate.to("/"))
            ui.label("Можно закрыть — отчёт появится в Библиотеке").classes("job-caption")

    def update() -> None:
        current = queue.get(job_id)
        if current is None:
            timer.deactivate()
            return
        state["job"] = current
        kind = _kind(current)
        if kind != state["kind"]:
            state["kind"] = kind
            stage.refresh()
        value = progress.percent(current)
        text, detail = progress.status(current)
        if kind == "queued":
            text = f"В очереди, ты {progress.position(current, queue.active())}-й"
            detail = "начну, когда закончится текущее исследование"
        pct_row.set_visibility(value is not None and kind in ("running", "done"))
        bar.set_visibility(value is not None and kind in ("running", "done"))
        pct.set_text(str(value or 0))
        fill.style(f"width: {value or 0}%")
        status.set_text("Готово" if kind == "done" else ("Остановлено" if kind == "stopped" else text))
        sub.set_text(" · ".join(part for part in (detail, progress.remaining(current, value)) if part) if kind == "running" else
                     (detail if kind == "queued" else ""))
        status.set_visibility(kind != "stopped")
        last = [url for url, _ in current["urls"][-3:]][::-1]
        if last != state["sources"]:
            state["sources"] = last
            recent.clear()
            with recent:
                for i, url in enumerate(last):
                    ui.label(kit.domain(url) + _path(url)).classes(f"r{i}")
        if kind == "done":
            timer.deactivate()
            ui.timer(1.2, lambda: ui.navigate.to(f"/report/{current['report_id']}"), once=True)
        elif kind == "stopped":
            timer.deactivate()

    timer = ui.timer(1.0, update, immediate=False)
    update()
    return None


def _path(url: str) -> str:
    """A short tail of the URL after its domain, for the list of the last sources."""
    tail = url.split("://", 1)[-1].split("/", 1)
    return ("/" + tail[1][:40]) if len(tail) > 1 and tail[1] else ""
