"""DEEper web app: a Perplexity-like UI for the research pipeline.

    uv run app.py      -> http://127.0.0.1:8501 (password only if APP_PASSWORD is set)

On Railway (PORT is set) it listens publicly, requires APP_PASSWORD, and also runs the
Telegram bot when its token is configured, so both share one report library.
Install on a Mac: open the URL in Safari -> File -> Add to Dock.
"""

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime
from urllib.parse import urlparse

from my_browser.pipeline import DEFAULT_SOURCES, REPORTS_DIR, ROOT

REPORTS_DIR.mkdir(exist_ok=True)
# Read by NiceGUI at import: keep login sessions on the Railway volume across redeploys.
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(REPORTS_DIR / ".nicegui"))

from fastapi import Request, Response  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from nicegui import app, ui  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

import bot  # noqa: E402
from my_browser import jobs, sources  # noqa: E402
from my_browser.ask import ask, load_qa  # noqa: E402
from my_browser.library import Library  # noqa: E402

PUBLIC = "PORT" in os.environ  # Railway sets PORT; locally we bind to 127.0.0.1
PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH = PUBLIC or bool(PASSWORD)
OPEN_PATHS = ("/login", "/static/", "/_nicegui", "/favicon")

MODES = {"detailed": "Подробный", "deep": "Глубокий", "quick": "Быстрый"}
MODE_HINTS = {
    "detailed": "Введение, отдельное исследование под каждую подтему, заключение — около 4 минут",
    "deep": "Рекурсивный поиск вширь и вглубь, затем один отчёт — дольше всех",
    "quick": "Один проход — около минуты",
}
SOURCE_LABELS = {"all": "Всё", "web": "Веб", "academic": "Наука"}
SCORE_COLORS = {"3/3": "positive", "2/3": "primary", "1/3": "warning"}

failed_logins: dict[str, list[float]] = {}
background: set[asyncio.Task] = set()

ui.add_head_html("""
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="DEEper">
<meta name="theme-color" content="#3730a3">
<style>
  .report { max-width: 860px; font-size: 16px; line-height: 1.65; }
  .report h1 { font-size: 1.9rem; font-weight: 700; line-height: 1.25; margin: .3em 0 .7em; }
  .report h2 { font-size: 1.4rem; font-weight: 650; margin: 1.6em 0 .5em; }
  .report h3 { font-size: 1.15rem; font-weight: 600; margin: 1.2em 0 .4em; }
  .report p, .report li { margin: .45em 0; }
  .report ul { list-style: disc; padding-left: 1.4em; }
  .report ol { list-style: decimal; padding-left: 1.4em; }
  .report table { border-collapse: collapse; margin: 1em 0; font-size: .92em; }
  .report th, .report td { border: 1px solid rgba(127,127,127,.35); padding: 6px 10px; vertical-align: top; }
  .report a { color: #6366f1; text-decoration: none; }
  .report a:hover { text-decoration: underline; }
  .report blockquote { border-left: 3px solid #6366f1; padding-left: 12px; opacity: .85; }
</style>
<script>
  // External links open outside the app (in the Safari web app: in Safari).
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href^="http"]');
    if (a && a.host !== location.host) { a.target = '_blank'; a.rel = 'noopener'; }
  }, true);
</script>
""", shared=True)
app.add_static_files("/static", ROOT / "static")


@app.add_middleware
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not AUTH or request.url.path.startswith(OPEN_PATHS) or app.storage.user.get("ok"):
            return await call_next(request)
        return RedirectResponse(f"/login?redirect_to={request.url.path}")


def storage_secret() -> str:
    path = REPORTS_DIR / ".storage_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


def mmss(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def domain(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def frame(title: str = "DEEper") -> None:
    """Header and the history drawer shared by all pages."""
    ui.page_title(title)
    ui.colors(primary="#4f46e5")

    with ui.header().classes("items-center px-3 py-2 bg-indigo-800"):
        ui.button(icon="menu", on_click=lambda: drawer.toggle()).props('flat round color=white aria-label="Меню"')
        ui.link("DEEper", "/").classes("text-white text-lg font-semibold no-underline")
        ui.space()
        ui.button(icon="block", on_click=blocked_dialog).props('flat round color=white aria-label="Заблокированные источники"').tooltip("Заблокированные источники")
        if AUTH:
            ui.button(icon="logout", on_click=logout).props('flat round color=white aria-label="Выйти"').tooltip("Выйти")

    with ui.left_drawer(value=True, bordered=True).classes("p-3 gap-2") as drawer:
        ui.button("Новое исследование", icon="add", on_click=lambda: ui.navigate.to("/")).props("unelevated no-caps").classes("w-full")
        search = ui.input(placeholder="Поиск по отчётам", on_change=lambda: history.refresh()).props("dense outlined clearable").classes("w-full")

        @ui.refreshable
        def history() -> None:
            with ui.list().props("dense").classes("w-full"):
                for job in jobs.active():
                    with ui.item(on_click=lambda j=job: ui.navigate.to(f"/job/{j.id}")).classes("rounded-lg"):
                        with ui.item_section().props("avatar"):
                            ui.spinner(size="sm")
                        with ui.item_section():
                            ui.item_label(job.query).props("lines=2")
                            ui.item_label(f"{MODES[job.mode]} · {'идёт' if job.status == 'running' else 'в очереди'}").props("caption")
                library = Library()
                entries = [e for e, _ in library.search(search.value)] if search.value else library.recent(50)
                if not entries and search.value:
                    ui.label("Ничего не нашлось").classes("text-sm text-gray-500 p-2")
                for e in entries:
                    with ui.item(on_click=lambda e=e: ui.navigate.to(f"/report/{e.id}")).classes("rounded-lg"):
                        with ui.item_section():
                            ui.item_label(e.query).props("lines=2")
                            ui.item_label(f"{e.created[:10]} · {MODES.get(e.mode, e.mode)} · {e.words} слов").props("caption")

        history()

    # Refresh the list when jobs start or finish, and announce finished ones.
    seen = {"active": {j.id for j in jobs.active()}}

    def watch() -> None:
        now_active = {j.id for j in jobs.active()}
        for job_id in seen["active"] - now_active:
            job = jobs.JOBS.get(job_id)
            if job and job.status == "done":
                ui.notify(f"Отчёт готов: {job.query}", type="positive", position="top-right")
                ui.run_javascript(
                    "if (window.Notification && Notification.permission === 'granted') "
                    f"new Notification('DEEper: отчёт готов', {{body: {json.dumps(job.query)}}})"
                )
        if now_active != seen["active"]:
            seen["active"] = now_active
            history.refresh()

    ui.timer(2.0, watch)


def logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


def blocked_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Заблокированные источники").classes("text-lg font-semibold")
        ui.label("Соцсети, видео и магазины отсеиваются всегда, остальное оценивает модель. "
                 "Сюда добавляйте сайты, которые не должны попадать в отчёты.").classes("text-sm text-gray-500")

        @ui.refreshable
        def listing() -> None:
            for d in sources.blocked_domains():
                with ui.row().classes("items-center w-full"):
                    ui.label(d).classes("flex-grow")
                    ui.button(icon="close", on_click=lambda d=d: (sources.unblock(d), listing.refresh())).props(f'flat round dense aria-label="Разблокировать {d}"')

        listing()

        def add() -> None:
            if new.value.strip():
                sources.block(new.value.strip())
                new.value = ""
                listing.refresh()

        with ui.row().classes("items-center w-full"):
            new = ui.input(placeholder="example.com").props("dense outlined").classes("flex-grow").on("keydown.enter", add)
            ui.button(icon="add", on_click=add).props('unelevated round dense aria-label="Добавить"')
        ui.button("Закрыть", on_click=dialog.close).props("flat").classes("self-end")
    dialog.open()


@ui.page("/login")
def login_page(redirect_to: str = "/") -> RedirectResponse | None:
    if not AUTH or app.storage.user.get("ok"):
        return RedirectResponse("/")
    ui.page_title("Вход — DEEper")

    def try_login() -> None:
        ip = ui.context.client.request.client.host if ui.context.client.request.client else "?"
        recent = [t for t in failed_logins.get(ip, []) if time.time() - t < 300]
        if len(recent) >= 5:
            ui.notify("Слишком много попыток, подождите 5 минут", type="negative")
            return
        if PASSWORD and secrets.compare_digest(password.value.encode(), PASSWORD.encode()):
            app.storage.user["ok"] = True
            ui.navigate.to(redirect_to if redirect_to.startswith("/") else "/")
        else:
            failed_logins[ip] = recent + [time.time()]
            ui.notify("Неверный пароль", type="negative")

    with ui.card().classes("absolute-center w-80 items-stretch gap-3"):
        ui.image("/static/icon-192.png").classes("w-16 h-16 self-center")
        ui.label("DEEper").classes("text-xl font-semibold self-center")
        if not PASSWORD:
            ui.label("На сервере не задан APP_PASSWORD — задайте его в переменных окружения.").classes("text-sm text-red-600")
        password = ui.input("Пароль", password=True, password_toggle_button=True).props("autofocus").on("keydown.enter", try_login)
        ui.button("Войти", on_click=try_login).props("unelevated")
    return None


@ui.page("/")
def index_page() -> None:
    frame()

    def start() -> None:
        query = (topic.value or "").strip()
        if not query:
            ui.notify("Напишите тему исследования", type="warning")
            return
        job = jobs.submit(query, mode.value, source.value, origin="web")
        ui.run_javascript("window.Notification && Notification.permission === 'default' && Notification.requestPermission()")
        ui.navigate.to(f"/job/{job.id}")

    with ui.column().classes("w-full max-w-3xl mx-auto mt-12 gap-4 px-4"):
        ui.label("Что исследуем?").classes("text-3xl font-semibold")
        topic = ui.textarea(placeholder="Например: перспективы натрий-ионных аккумуляторов до 2030 года") \
            .props("outlined autogrow autofocus").classes("w-full text-lg")
        topic.on("keydown.meta.enter", start)
        topic.on("keydown.ctrl.enter", start)
        with ui.row().classes("items-center gap-3 w-full"):
            mode = ui.toggle(MODES, value="detailed").props("no-caps rounded unelevated toggle-color=primary")
            source = ui.toggle(SOURCE_LABELS, value=DEFAULT_SOURCES).props("no-caps rounded unelevated toggle-color=primary")
            ui.space()
            ui.button("Исследовать", icon="travel_explore", on_click=start).props("unelevated size=lg no-caps")
        hint = ui.label(MODE_HINTS["detailed"]).classes("text-sm text-gray-500")
        mode.on_value_change(lambda e: hint.set_text(MODE_HINTS[e.value]))
        ui.label("Запросы к поиску формируются на английском, отчёт пишется на русском. ⌘+Enter — запуск.") \
            .classes("text-xs text-gray-400")


@ui.page("/job/{job_id}")
def job_page(job_id: str) -> RedirectResponse | None:
    job = jobs.JOBS.get(job_id)
    if job and job.status == "done":
        return RedirectResponse(f"/report/{job.report_id}")
    frame("Исследование — DEEper")
    if not job:
        with ui.column().classes("w-full max-w-3xl mx-auto mt-12 px-4"):
            ui.label("Задача не найдена: возможно, сервер перезапускался. Готовые отчёты — в списке слева.")
        return None

    with ui.column().classes("w-full max-w-3xl mx-auto mt-8 gap-4 px-4"):
        ui.label(job.query).classes("text-2xl font-semibold")
        with ui.row().classes("gap-2"):
            ui.chip(MODES[job.mode], icon="description").props("outline")
            ui.chip(SOURCE_LABELS.get(job.sources, job.sources), icon="travel_explore").props("outline")
            if job.origin == "telegram":
                ui.chip("из Telegram", icon="send").props("outline")
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 w-full"):
                spinner = ui.spinner(size="md")
                phase = ui.label().classes("text-base")
                ui.space()
                clock = ui.label().classes("font-mono text-gray-500")
        error_box = ui.column().classes("w-full")
        ui.label("Поисковые запросы").classes("text-sm font-semibold text-gray-500 mt-2")
        queries_box = ui.column().classes("gap-1")
        sources_title = ui.label("Источники").classes("text-sm font-semibold text-gray-500 mt-2")
        sources_box = ui.column().classes("gap-1 w-full")
        with ui.expansion("Журнал", icon="list").classes("w-full"):
            log = ui.log(max_lines=300).classes("w-full h-64 text-xs")

    shown = {"queries": 0, "urls": 0, "log": 0}

    def update() -> None:
        phase.set_text("В очереди: дождусь окончания текущего исследования" if job.status == "queued" else job.phase)
        clock.set_text(mmss(job.elapsed))
        with queries_box:
            for q in job.queries[shown["queries"]:]:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("search", size="xs").classes("text-gray-400")
                    ui.label(q).classes("text-sm")
        shown["queries"] = len(job.queries)
        with sources_box:
            for url, score in job.urls[shown["urls"]:]:
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.badge(score or "–", color=SCORE_COLORS.get(score, "grey")).props("rounded")
                    ui.link(domain(url), url, new_tab=True).classes("text-sm font-medium")
                    ui.label(url).classes("text-xs text-gray-400 truncate")
        shown["urls"] = len(job.urls)
        sources_title.set_text(f"Источники ({len(job.urls)})")
        new_lines = job.log_count - shown["log"]
        for line in list(job.log)[-min(new_lines, len(job.log)):] if new_lines > 0 else []:
            log.push(line)
        shown["log"] = job.log_count

        if job.status == "done":
            timer.deactivate()
            ui.navigate.to(f"/report/{job.report_id}")
        elif job.status == "error":
            timer.deactivate()
            spinner.set_visibility(False)
            with error_box, ui.card().classes("w-full bg-red-50"):
                ui.label(f"Ошибка: {job.error}").classes("text-red-700")
                ui.button("Попробовать снова", icon="refresh", on_click=lambda: ui.navigate.to(
                    f"/job/{jobs.submit(job.query, job.mode, job.sources).id}")).props("unelevated no-caps")

    timer = ui.timer(1.0, update, immediate=False)
    update()
    return None


@ui.page("/report/{report_id}")
def report_page(report_id: int) -> None:
    frame("Отчёт — DEEper")
    library = Library()
    entry = library.get(report_id)
    if not entry or not entry.md_path.exists():
        with ui.column().classes("w-full max-w-3xl mx-auto mt-12 px-4"):
            ui.label("Отчёт не найден.")
        return
    markdown = entry.md_path.read_text("utf-8")
    meta = json.loads(entry.base.with_suffix(".json").read_text("utf-8"))
    qa = load_qa(entry.md_path)
    blocked = set(sources.blocked_domains())

    def delete() -> None:
        library.delete(entry.id)
        ui.navigate.to("/")

    with ui.dialog() as confirm, ui.card():
        ui.label(f"Удалить отчёт «{entry.query}» вместе с файлами?")
        with ui.row().classes("self-end"):
            ui.button("Отмена", on_click=confirm.close).props("flat")
            ui.button("Удалить", on_click=delete).props("unelevated color=negative")

    with ui.column().classes("w-full max-w-4xl mx-auto mt-6 gap-3 px-4 pb-16"):
        with ui.row().classes("items-center w-full gap-2"):
            created = datetime.fromisoformat(entry.created).strftime("%d.%m.%Y %H:%M")
            for text, icon in ((created, "event"), (MODES.get(entry.mode, entry.mode), "description"),
                               (f"{entry.words} слов", "notes"), (f"{entry.sources_count} источников", "link"),
                               (mmss(entry.duration_sec), "timer")):
                ui.chip(text, icon=icon).props("outline dense")
            ui.space()
            for kind, icon in (("pdf", "picture_as_pdf"), ("docx", "description"), ("md", "article")):
                if entry.base.with_suffix(f".{kind}").exists():
                    ui.button(kind.upper(), icon=icon, on_click=lambda k=kind: ui.download.from_url(f"/files/{entry.id}/{k}")) \
                        .props("flat dense no-caps")
            ui.button(icon="delete", on_click=confirm.open).props('flat round dense color=negative aria-label="Удалить отчёт"').tooltip("Удалить отчёт")

        with ui.tabs().props("no-caps").classes("w-full") as tabs:
            ui.tab("report", label="Отчёт", icon="article")
            ui.tab("sources", label=f"Источники ({len(meta['sources'])})", icon="link")
            qa_tab = ui.tab("qa", label=f"Вопросы ({len(qa)})", icon="forum")

        with ui.tab_panels(tabs, value="report").classes("w-full bg-transparent"):
            with ui.tab_panel("report").classes("px-0"):
                ui.markdown(markdown).classes("report")

            with ui.tab_panel("sources").classes("px-0"):
                def block(d: str, button) -> None:
                    sources.block(d)
                    button.disable()
                    ui.notify(f"{d} больше не будет источником", type="info")

                for url in meta["sources"]:
                    d = domain(url)
                    with ui.row().classes("items-center w-full gap-2 no-wrap"):
                        ui.link(d, url, new_tab=True).classes("text-sm font-medium")
                        ui.label(url).classes("text-xs text-gray-400 truncate flex-grow")
                        b = ui.button(icon="block").props(f'flat round dense size=sm aria-label="Не использовать {d}"').tooltip(f"Не использовать {d}")
                        b.on_click(lambda d=d, b=b: block(d, b))
                        if d in blocked:
                            b.disable()

            with ui.tab_panel("qa").classes("px-0"):
                qa_box = ui.column().classes("w-full gap-4")

                def show(q: str, a: str) -> None:
                    with qa_box, ui.card().classes("w-full"):
                        ui.label(q).classes("font-semibold")
                        ui.markdown(a).classes("report")

                for item in qa:
                    show(item["q"], item["a"])

                async def submit() -> None:
                    q = (question.value or "").strip()
                    if not q:
                        return
                    send.props("loading")
                    question.disable()
                    try:
                        answer = await ask(entry.md_path, q)
                    except Exception as e:
                        ui.notify(f"Ошибка: {e}", type="negative")
                        return
                    finally:
                        send.props(remove="loading")
                        question.enable()
                    show(q, answer)
                    question.value = ""
                    qa_tab.props(f'label="Вопросы ({len(load_qa(entry.md_path))})"')

                with ui.row().classes("w-full items-end no-wrap gap-2"):
                    question = ui.textarea(placeholder="Вопрос по отчёту — ответ строится только по его тексту") \
                        .props("outlined autogrow").classes("flex-grow")
                    question.on("keydown.meta.enter", submit)
                    question.on("keydown.ctrl.enter", submit)
                    send = ui.button(icon="send", on_click=submit).props('unelevated round aria-label="Спросить"')


@app.get("/files/{report_id}/{kind}")
async def report_file(report_id: int, kind: str):
    entry = Library().get(report_id)
    path = entry.base.with_suffix(f".{kind}") if entry and kind in ("pdf", "docx", "md") else None
    if not path or not path.exists():
        return Response(status_code=404)
    return FileResponse(path, filename=path.name)


async def on_startup() -> None:
    Library().sync()
    if bot.token():
        task = asyncio.create_task(bot.start(handle_signals=False))
        background.add(task)
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or logging.error("Bot stopped: %r", t.exception()))


app.on_startup(on_startup)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host, port = ("0.0.0.0" if PUBLIC else "127.0.0.1"), int(os.environ.get("PORT", 8501))  # 8080 is the local SearXNG
    logging.info("Веб-интерфейс: http://%s:%s, пароль %s", host, port, "задан" if PASSWORD else ("НЕ задан" if PUBLIC else "не нужен"))
    ui.run(
        host=host,
        port=port,
        title="DEEper",
        favicon=ROOT / "static" / "favicon.png",
        dark=None,
        language="ru",
        storage_secret=storage_secret(),
        reload=False,
        show=False,
        show_welcome_message=not PUBLIC,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
