"""Core's web app: a Perplexity-like UI for DEEper, styled per the AVEN brandbook, plus core's API.

    uv run app.py                    -> everything in one process at http://127.0.0.1:8501
    uv run python -m my_browser.core -> core alone (web app + API); the worker and bot run elsewhere

On Railway (PORT is set) it listens publicly and requires APP_PASSWORD; /api/* takes the service
token instead. Install on a Mac: open the URL in Safari -> File -> Add to Dock.

Brand (aven-brandbook.pdf): night #14100E background, paper #F2EBE1 text (never pure white),
bordo #7A2A35 fills only (never text on dark), amber #E8A24A as the single accent per screen;
Unbounded for 2-4 word headings, Onest for text, JetBrains Mono for labels and numbers.
"""

import html
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode, urlparse

from my_browser.common.settings import (DEFAULT_SOURCES, DEFAULT_WRITER, FACTCHECK_MODES, PORT, REPORTS_DIR, ROOT,
                                        configure_logging)

REPORTS_DIR.mkdir(exist_ok=True)
# Read by NiceGUI at import: keep login sessions on the Railway volume across redeploys.
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(REPORTS_DIR / ".nicegui"))

from fastapi import Request, Response  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from nicegui import app, ui  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from my_browser.common.report import ANY_ANSWER, FACTCHECK_TITLE, KINDS, VERDICT_TEXT, appended, body  # noqa: E402
from my_browser.common.text import plural, rub_text  # noqa: E402
from my_browser.core import blocklist, queue, service  # noqa: E402
from my_browser.core.api import router  # noqa: E402
from my_browser.core.library import Library  # noqa: E402
from my_browser.core.qa import load_qa  # noqa: E402

PUBLIC = "PORT" in os.environ  # Railway sets PORT; locally we bind to 127.0.0.1
PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH = PUBLIC or bool(PASSWORD)
OPEN_PATHS = ("/login", "/static/", "/_nicegui", "/favicon", "/api/")  # /api checks the service token itself

MODES = {"detailed": "Подробный", "deep": "Глубокий", "quick": "Быстрый"}
MODE_HINTS = {
    "detailed": "≈ 4 мин с Flash, ≈ 18 мин с Pro · введение, отдельное исследование каждой подтемы, заключение",
    "deep": "дольше всех · рекурсивный поиск вширь и вглубь, затем один отчёт",
    "quick": "≈ 1–4 мин · один проход",
}
SOURCE_LABELS = {"all": "Всё", "web": "Веб", "academic": "Наука"}
WRITER_LABELS = {"pro": "Pro", "flash": "Flash"}
WRITER_HINTS = {"pro": "текст пишет DeepSeek V4 Pro — сильнее, дороже ≈ 3×", "flash": "текст пишет DeepSeek Flash — быстрее и дешевле"}
WRITER_OF_MODEL = {"deepseek-v4-pro": "pro", "deepseek-flash": "flash"}
FACTCHECK_HINT = "сверить утверждения со ссылками с источниками и исправить расхождения · +1–2 мин"
BRAND_COLORS = dict(
    primary="#7A2A35", secondary="#F2EBE1", accent="#E8A24A", dark="#1D1916", dark_page="#14100E",
    positive="#7A2A35", negative="#7A2A35", info="#7A2A35", warning="#E8A24A",
    night="#14100E", paper="#F2EBE1", bordo="#7A2A35", amber="#E8A24A",
)

failed_logins: dict[str, list[float]] = {}

ui.add_head_html("""
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="DEEper">
<meta name="theme-color" content="#14100E">
<style>
  @font-face { font-family: 'Onest'; src: url('/static/fonts/Onest.ttf') format('truetype'); font-weight: 100 900; font-display: swap; }
  @font-face { font-family: 'Unbounded'; src: url('/static/fonts/Unbounded.ttf') format('truetype'); font-weight: 200 900; font-display: swap; }
  @font-face { font-family: 'JetBrains Mono'; src: url('/static/fonts/JetBrainsMono.ttf') format('truetype'); font-weight: 100 800; font-display: swap; }
  :root {
    --night: #14100E; --night-2: #1D1916; --paper: #F2EBE1; --bordo: #7A2A35; --amber: #E8A24A;
    --paper-80: rgba(242,235,225,.8); --paper-60: rgba(242,235,225,.6); --paper-40: rgba(242,235,225,.4);
    --line: rgba(242,235,225,.1); --surface: rgba(242,235,225,.035); --surface-2: rgba(242,235,225,.06);
  }
  html, body { background: var(--night); }
  body {
    color: var(--paper); font-family: 'Onest', system-ui, sans-serif; -webkit-font-smoothing: antialiased;
    background: radial-gradient(1100px 560px at 88% -12%, rgba(122,42,53,.32), transparent 62%) fixed, var(--night);
  }
  .q-layout, .q-page-container, .q-page, .nicegui-content { background: transparent !important; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
  .display { font-family: 'Unbounded', sans-serif; font-weight: 700; letter-spacing: -.01em; }
  .brand-label { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .2em; text-transform: uppercase; color: var(--paper-40); }
  .brand-label.amber { color: var(--amber); }
  .muted { color: var(--paper-60) !important; }
  .faint { color: var(--paper-40) !important; }
  .amber-text { color: var(--amber) !important; }

  .body--dark, .body--dark .q-item, .body--dark .q-tab, .body--dark .q-card,
  .body--dark .q-field__native, .body--dark .q-field__input, .body--dark .q-btn { color: var(--paper); }
  .body--dark .q-field__label, .body--dark textarea::placeholder, .body--dark input::placeholder { color: var(--paper-40) !important; }
  .body--dark .q-field--outlined .q-field__control { background: var(--surface); border-radius: 12px; }
  .body--dark .q-field--outlined .q-field__control:before { border-color: var(--line); }
  .body--dark .q-field--outlined:hover .q-field__control:before { border-color: var(--paper-40); }
  .body--dark .q-field--outlined.q-field--focused .q-field__control:after { border-color: var(--paper-40); border-width: 1px; }
  .body--dark .q-card { background: var(--surface); border: 1px solid var(--line); box-shadow: none; border-radius: 14px; }
  .body--dark .q-dialog .q-card { background: var(--night-2); }
  .body--dark .q-drawer { background: var(--night); }
  .body--dark .q-drawer--bordered { border-color: var(--line) !important; }
  .body--dark .q-item { border-radius: 10px; }
  .q-tab { color: var(--paper-60); text-transform: none; }
  .q-tab--active { color: var(--paper); }
  .q-tab__indicator { background: var(--amber); height: 2px; }
  .q-tab__label { font-family: 'Onest'; font-weight: 500; }
  .q-notification { color: var(--paper) !important; font-family: 'Onest'; }
  .q-btn { text-transform: none; font-weight: 500; letter-spacing: 0; }
  .q-btn-group .q-btn { border-radius: 999px !important; padding: 6px 16px; min-height: 36px; }
  .q-tab .q-focus-helper, .q-tab .q-ripple { display: none !important; }
  .q-expansion-item .q-item { color: var(--paper-60); }
  ::selection { background: rgba(232,162,74,.35); }

  .brand-chip { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--paper-60);
                border: 1px solid var(--line); border-radius: 999px; padding: 3px 11px; white-space: nowrap; }
  .score { font-family: 'JetBrains Mono', monospace; font-size: 11px; border-radius: 999px; padding: 1px 8px;
           border: 1px solid var(--line); color: var(--paper-40); white-space: nowrap; }
  .score-3 { background: var(--bordo); border-color: var(--bordo); color: var(--paper); }
  .score-2 { border-color: var(--paper-40); color: var(--paper-80); }
  .url { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--paper-40); }
  a.plain { color: var(--paper); text-decoration: none; }
  a.plain:hover { color: var(--amber); }
  a.next { display: block; color: var(--paper); text-decoration: none; background: var(--surface);
           border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; transition: border-color .15s; }
  a.next:hover { border-color: var(--paper-40); }
  .kind { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--paper-40); }

  .report { max-width: 820px; font-size: 16.5px; line-height: 1.72; color: rgba(242,235,225,.92); }
  .report h1 { font-weight: 700; font-size: 2rem; line-height: 1.22; margin: .2em 0 .8em; color: var(--paper); letter-spacing: -.01em; }
  .report h2 { font-weight: 650; font-size: 1.4rem; line-height: 1.3; margin: 1.8em 0 .55em; color: var(--paper); }
  .report h3 { font-weight: 600; font-size: 1.1rem; margin: 1.3em 0 .4em; color: var(--paper); }
  .report p, .report li { margin: .5em 0; }
  .report ul { list-style: disc; padding-left: 1.4em; }
  .report ol { list-style: decimal; padding-left: 1.4em; }
  .report li::marker { color: var(--paper-40); }
  .report strong { color: var(--paper); font-weight: 650; }
  .report a { color: var(--paper); text-decoration: underline; text-decoration-color: var(--amber);
              text-decoration-thickness: 1px; text-underline-offset: 3px; }
  .report a:hover { color: var(--amber); }
  .report table { border-collapse: collapse; margin: 1.2em 0; font-size: .92em; }
  .report th, .report td { border: 1px solid var(--line); padding: 7px 11px; vertical-align: top; }
  .report th { background: var(--surface-2); font-weight: 600; }
  .report blockquote { border-left: 3px solid var(--bordo); padding-left: 14px; color: var(--paper-80); }
  .report code { font-family: 'JetBrains Mono', monospace; font-size: .88em; background: var(--surface-2); padding: 1px 5px; border-radius: 4px; }
  .report hr { border: none; border-top: 1px solid var(--line); margin: 2em 0; }
  .report.compact { font-size: 15.5px; }
  .report sup.fc-mark a { background: var(--bordo); color: var(--paper); text-decoration: none; border-radius: 999px;
                          font-family: 'JetBrains Mono', monospace; font-size: 10px; padding: 1px 6px; margin-left: 3px; }
  .q-toggle__label { color: var(--paper-80); }
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
app.include_router(router)


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


def number(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def domain(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def label(text: str, amber: bool = False) -> None:
    ui.label(text).classes("brand-label" + (" amber" if amber else ""))


def chip(text: str) -> ui.label:
    return ui.label(text).classes("brand-chip")


def mark_doubtful(markdown: str, doubtful: list[dict]) -> str:
    """A bordo «?» after each sentence the fact-check could not confirm; it shows the verdict and leads to the list."""
    for item in doubtful:
        if not item.get("raw"):
            continue
        tip = html.escape(f"{VERDICT_TEXT.get(item['verdict'], item['verdict'])}: {item['note']}" if item["note"]
                          else VERDICT_TEXT.get(item["verdict"], item["verdict"]), quote=True)
        mark = f' <sup class="fc-mark" title="{tip}"><a href="#factcheck">?</a></sup>'
        pattern = r"\s+".join(map(re.escape, item["raw"].split()))
        markdown = re.sub(f"({pattern})(?! <sup class=\"fc-mark\")", lambda m: m.group(1) + mark, markdown, count=1)
    return markdown


def follow_up_url(topic: str, mode: str, parent: int) -> str:
    """New-research form pre-filled with a topic that continues report `parent`."""
    return "/?" + urlencode({"topic": topic, "mode": mode, "parent": parent})


def primary_button(text: str, on_click, icon: str | None = None):
    return ui.button(text, icon=icon, on_click=on_click).props("unelevated no-caps color=bordo text-color=paper").classes("rounded-xl px-5")


def frame(title: str = "DEEper") -> None:
    """Header and the history drawer shared by all pages."""
    ui.page_title(title)
    ui.colors(**BRAND_COLORS)

    with ui.header().classes("items-center px-4 py-2 gap-2").style(
            "background: rgba(20,16,14,.82); backdrop-filter: blur(10px); border-bottom: 1px solid var(--line)"):
        ui.button(icon="menu", on_click=lambda: drawer.toggle()).props('flat round dense color=paper aria-label="Меню"').classes("opacity-70")
        with ui.link(target="/").classes("flex items-center gap-3 no-underline"):
            ui.image("/static/aven-mark.svg").classes("w-7 h-7")
            ui.label("DEEPER").classes("display text-[16px] tracking-wide").style("color: var(--paper)")
            ui.label("made by aven").classes("mono text-[11px] faint mt-[3px] gt-xs")
        ui.space()
        ui.button(icon="block", on_click=blocked_dialog).props('flat round dense color=paper aria-label="Заблокированные источники"') \
            .classes("opacity-70").tooltip("Заблокированные источники")
        if AUTH:
            ui.button(icon="logout", on_click=logout).props('flat round dense color=paper aria-label="Выйти"').classes("opacity-70").tooltip("Выйти")

    # value=None: open on wide screens, closed on phones (Quasar breakpoint).
    with ui.left_drawer(value=None, bordered=True).classes("p-4 gap-3") as drawer:
        primary_button("Новое исследование", lambda: ui.navigate.to("/"), icon="add").classes("w-full")
        search = ui.input(placeholder="Поиск по отчётам", on_change=lambda: history.refresh()) \
            .props("dense outlined clearable dark").classes("w-full")
        label("История")

        @ui.refreshable
        def history() -> None:
            with ui.list().props("dense").classes("w-full gap-1"):
                for job in queue.active():
                    with ui.item(on_click=lambda j=job: ui.navigate.to(f"/job/{j['id']}")).classes("py-2"):
                        with ui.item_section().props("avatar").classes("min-w-0 pr-2"):
                            ui.spinner(size="xs", color="paper")
                        with ui.item_section():
                            ui.item_label(job["query"]).props("lines=2").classes("text-[14px]")
                            ui.item_label(f"{MODES[job['mode']].lower()} · {'идёт' if job['status'] == 'running' else 'в очереди'}") \
                                .classes("mono text-[11px] faint")
                library = Library()
                entries = [e for e, _ in library.search(search.value)] if search.value else library.recent(50)
                if not entries and search.value:
                    ui.label("Ничего не нашлось").classes("text-sm faint px-3 py-2")
                for e in entries:
                    with ui.item(on_click=lambda e=e: ui.navigate.to(f"/report/{e.id}")).classes("py-2"):
                        with ui.item_section():
                            ui.item_label(e.query).props("lines=2").classes("text-[14px]").style("color: var(--paper-80)")
                            ui.item_label(f"{datetime.fromisoformat(e.created):%d.%m} · {MODES.get(e.mode, e.mode).lower()} · "
                                          f"{number(e.words)} {plural(e.words, 'слово', 'слова', 'слов')}") \
                                .classes("mono text-[11px] faint")

        history()

    # Refresh the list when jobs start, move from the queue to work, or finish; announce finished ones.
    seen = {"active": {j["id"]: j["status"] for j in queue.active()}}

    def watch() -> None:
        now_active = {j["id"]: j["status"] for j in queue.active()}
        for job_id in seen["active"].keys() - now_active.keys():
            job = queue.get(job_id)
            if job and job["status"] == "done":
                ui.notify(f"Отчёт готов: {job['query']}", type="positive", position="top-right")
                ui.run_javascript(
                    "if (window.Notification && Notification.permission === 'granted') "
                    f"new Notification('DEEper: отчёт готов', {{body: {json.dumps(job['query'])}}})"
                )
        if now_active != seen["active"]:
            seen["active"] = now_active
            history.refresh()

    ui.timer(2.0, watch)


def logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


def blocked_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[420px] gap-3 p-6"):
        label("Источники")
        ui.label("Заблокированные сайты").classes("text-lg font-semibold")
        ui.label("Соцсети, видео и магазины отсеиваются всегда, остальное оценивает модель. "
                 "Сюда — сайты, которые не должны попадать в отчёты.").classes("text-sm muted")

        @ui.refreshable
        def listing() -> None:
            domains = blocklist.blocked_domains()
            if not domains:
                ui.label("Список пуст").classes("mono text-xs faint")
            for d in domains:
                with ui.row().classes("items-center w-full no-wrap"):
                    ui.label(d).classes("mono text-sm flex-grow")
                    ui.button(icon="close", on_click=lambda d=d: (blocklist.unblock(d), listing.refresh())) \
                        .props(f'flat round dense size=sm color=paper aria-label="Разблокировать {d}"').classes("opacity-60")

        listing()

        def add() -> None:
            if new.value.strip():
                blocklist.block(new.value.strip())
                new.value = ""
                listing.refresh()

        with ui.row().classes("items-center w-full no-wrap gap-2"):
            new = ui.input(placeholder="example.com").props("dense outlined dark").classes("flex-grow mono").on("keydown.enter", add)
            ui.button(icon="add", on_click=add).props('unelevated round dense color=bordo text-color=paper aria-label="Добавить"')
        ui.button("Закрыть", on_click=dialog.close).props("flat no-caps color=paper").classes("self-end opacity-70")
    dialog.open()


@ui.page("/login")
def login_page(redirect_to: str = "/") -> RedirectResponse | None:
    if not AUTH or app.storage.user.get("ok"):
        return RedirectResponse("/")
    ui.page_title("Вход — DEEper")
    ui.colors(**BRAND_COLORS)

    def try_login() -> None:
        ip = ui.context.client.request.client.host if ui.context.client.request.client else "?"
        recent = [t for t in failed_logins.get(ip, []) if time.time() - t < 300]
        if len(recent) >= 5:
            ui.notify("Слишком много попыток. Подождите 5 минут", type="warning")
            return
        if PASSWORD and secrets.compare_digest(password.value.encode(), PASSWORD.encode()):
            app.storage.user["ok"] = True
            ui.navigate.to(redirect_to if redirect_to.startswith("/") else "/")
        else:
            failed_logins[ip] = recent + [time.time()]
            ui.notify("Неверный пароль", type="negative")

    with ui.column().classes("absolute-center items-center gap-6 w-80"):
        ui.image("/static/aven-mark.svg").classes("w-16 h-16")
        with ui.column().classes("items-center gap-1"):
            ui.label("DEEPER").classes("display text-3xl")
            ui.label("made by aven").classes("mono text-xs faint")
        if not PASSWORD:
            ui.label("На сервере не задан APP_PASSWORD — задайте его в переменных окружения.").classes("text-sm text-center muted")
        password = ui.input("Пароль", password=True, password_toggle_button=True) \
            .props("outlined dark autofocus").classes("w-full").on("keydown.enter", try_login)
        primary_button("Войти", try_login).classes("w-full")
    return None


@ui.page("/")
def index_page(topic: str = "", mode: str = "detailed", parent: int | None = None) -> None:
    """New research. Follow-up links pre-fill the topic and mode, and `parent` is the report it continues."""
    frame()
    origin = Library().get(parent) if parent else None
    toggle_props = "no-caps rounded unelevated dense color=dark text-color=paper toggle-color=bordo toggle-text-color=paper"

    def start() -> None:
        query = (topic_field.value or "").strip()
        if not query:
            ui.notify("Напишите тему исследования", type="warning")
            return
        parent_id = origin.id if origin and link_row.visible else None
        job = service.submit(query, mode_toggle.value, source.value, writer.value, parent_id=parent_id, factcheck=check.value)
        ui.run_javascript("window.Notification && Notification.permission === 'default' && Notification.requestPermission()")
        ui.navigate.to(f"/job/{job['id']}")

    async def clarify() -> None:
        query = (topic_field.value or "").strip()
        if not query:
            ui.notify("Напишите тему исследования", type="warning")
            return
        clarify_button.props("loading")
        try:
            result = await service.clarify(query)
            questions, rub = result["questions"], result["cost_rub"]
        except Exception as e:
            ui.notify(f"Не получилось: {e}", type="negative")
            return
        finally:
            clarify_button.props(remove="loading")
        if not questions:
            ui.notify("Уточнять нечего: тема и так конкретная", type="info")
            return
        answers = []

        async def apply() -> None:
            chosen = [(question, toggle.value) for question, toggle in answers if toggle.value != ANY_ANSWER]
            if chosen:
                apply_button.props("loading")
                try:
                    result = await service.refine(query, chosen)
                    refined, extra = result["topic"], result["cost_rub"]
                except Exception as e:
                    ui.notify(f"Не получилось: {e}", type="negative")
                    return
                finally:
                    apply_button.props(remove="loading")
                topic_field.value = refined
                ui.notify(f"Тема уточнена · {rub_text(rub + extra)}", type="positive")
            clarify_box.clear()

        clarify_box.clear()
        with clarify_box, ui.card().classes("w-full px-5 py-4 gap-3"):
            label("Уточнение")
            for item in questions:
                ui.label(item["q"]).classes("text-[15px] font-medium")
                toggle = ui.toggle([*item["options"], ANY_ANSWER], value=ANY_ANSWER) \
                    .props(toggle_props).style("flex-wrap: wrap; gap: 6px")
                answers.append((item["q"], toggle))
            with ui.row().classes("items-center gap-2 w-full mt-1"):
                ui.label(f"вопросы: {rub_text(rub)}").classes("mono text-[11px] faint")
                ui.space()
                ui.button("Отмена", on_click=clarify_box.clear).props("flat no-caps color=paper").classes("opacity-70")
                apply_button = primary_button("Применить", apply)

    with ui.column().classes("w-full max-w-3xl mx-auto mt-16 gap-5 px-6"):
        label("Новое исследование", amber=True)
        ui.label("Что исследуем?").classes("display text-[38px] leading-tight")
        if origin:
            with ui.row().classes("items-center gap-2 no-wrap w-full -mt-2") as link_row:
                ui.icon("subdirectory_arrow_right").classes("faint")
                ui.link(f"продолжение отчёта #{origin.id}: {origin.query}", f"/report/{origin.id}") \
                    .classes("plain mono text-[12px] muted truncate")
                ui.button(icon="close", on_click=lambda: link_row.set_visibility(False)) \
                    .props('flat round dense size=sm color=paper aria-label="Не связывать с отчётом"').classes("opacity-50")
        topic_field = ui.textarea(value=topic, placeholder="Например: перспективы натрий-ионных аккумуляторов до 2030 года") \
            .props("outlined autogrow autofocus dark").classes("w-full text-[18px]")
        topic_field.on("keydown.meta.enter", start)
        topic_field.on("keydown.ctrl.enter", start)
        with ui.row().classes("items-center gap-3 w-full"):
            mode_toggle = ui.toggle(MODES, value=mode if mode in MODES else "detailed").props(toggle_props) \
                .classes("border border-[color:var(--line)] rounded-full")
            source = ui.toggle(SOURCE_LABELS, value=DEFAULT_SOURCES).props(toggle_props) \
                .classes("border border-[color:var(--line)] rounded-full")
            writer = ui.toggle(WRITER_LABELS, value=DEFAULT_WRITER).props(toggle_props) \
                .classes("border border-[color:var(--line)] rounded-full")
            ui.space()
            clarify_button = ui.button("Уточнить", icon="tune", on_click=clarify).props("flat no-caps color=paper") \
                .classes("opacity-80").tooltip("2–3 вопроса с вариантами, чтобы точнее сформулировать тему")
            primary_button("Исследовать", start, icon="arrow_forward").props("size=lg")
        clarify_box = ui.column().classes("w-full")
        hint = ui.label(MODE_HINTS[mode_toggle.value]).classes("mono text-[12px] muted")
        writer_hint = ui.label(WRITER_HINTS[DEFAULT_WRITER]).classes("mono text-[12px] muted -mt-3")
        writer.on_value_change(lambda e: writer_hint.set_text(WRITER_HINTS[e.value]))
        choice = {"own": False, "setting": False}  # the user's own switch position beats the mode's default

        def flipped() -> None:
            if not choice["setting"]:
                choice["own"] = True

        with ui.row().classes("items-center gap-3 no-wrap -mt-1"):
            check = ui.switch("Проверка фактов", value=mode_toggle.value in FACTCHECK_MODES, on_change=flipped) \
                .props("dense color=bordo keep-color").classes("whitespace-nowrap shrink-0")
            ui.label(FACTCHECK_HINT).classes("mono text-[12px] muted")

        def mode_changed(e) -> None:
            hint.set_text(MODE_HINTS[e.value])
            if not choice["own"]:
                choice["setting"] = True
                check.value = e.value in FACTCHECK_MODES
                choice["setting"] = False

        mode_toggle.on_value_change(mode_changed)
        ui.label("запросы к поиску — на английском · отчёт — на русском · ⌘ Enter — запуск").classes("mono text-[11px] faint")


@ui.page("/job/{job_id}")
def job_page(job_id: str) -> RedirectResponse | None:
    job = queue.get(job_id)
    if job and job["status"] == "done":
        return RedirectResponse(f"/report/{job['report_id']}")
    frame("Исследование — DEEper")
    if not job:
        with ui.column().classes("w-full max-w-3xl mx-auto mt-16 px-6"):
            ui.label("Задача не найдена. Готовые отчёты — в истории слева.").classes("muted")
        return None

    with ui.column().classes("w-full max-w-3xl mx-auto mt-12 gap-4 px-6"):
        label("Исследование")
        ui.label(job["query"]).classes("text-[26px] font-semibold leading-snug")
        with ui.row().classes("gap-2"):
            chip(MODES[job["mode"]].lower())
            chip(f"источники: {SOURCE_LABELS.get(job['sources'], job['sources']).lower()}")
            chip(f"текст: {job['writer']}")
            if job["parent"] and (origin := Library().by_name(job["parent"])):
                chip(f"продолжение #{origin.id}")
            if job["origin"] == "telegram":
                chip("из telegram")
            if job["factcheck"]:
                chip("проверка фактов")
        with ui.card().classes("w-full px-5 py-4"):
            with ui.row().classes("items-center gap-4 w-full no-wrap"):
                spinner = ui.spinner(size="sm", color="paper").classes("opacity-70")
                phase = ui.label().classes("text-[16px]")
                ui.space()
                spent = ui.label().classes("mono text-[13px] muted").tooltip("Расход на DeepSeek по реальным токенам")
                clock = ui.label().classes("mono text-[18px] amber-text")
        error_box = ui.column().classes("w-full")
        queries_title = ui.label().classes("brand-label mt-4")
        queries_box = ui.column().classes("gap-1")
        sources_title = ui.label().classes("brand-label mt-4")
        sources_box = ui.column().classes("gap-1 w-full")
        with ui.expansion("Журнал").classes("w-full mt-2").props("dense"):
            log = ui.log(max_lines=300).classes("w-full h-64 mono text-[11px]").style(
                "background: var(--surface); color: var(--paper-60); border: 1px solid var(--line); border-radius: 10px")

    shown = {"queries": 0, "urls": 0, "log": None}

    def again() -> None:
        queue.restart(job_id)
        ui.navigate.to(f"/job/{job_id}")

    def update() -> None:
        current = queue.get(job_id)
        if current is None:
            timer.deactivate()
            return
        phase.set_text("В очереди — дождусь окончания текущего исследования" if current["status"] == "queued" else current["phase"] or "")
        clock.set_text(mmss(current["elapsed"]))
        spent.set_text(rub_text(current["cost_rub"]) if current["cost_rub"] else "")
        queries_title.set_text(f"Поисковые запросы · {len(current['queries'])}")
        with queries_box:
            for q in current["queries"][shown["queries"]:]:
                with ui.row().classes("items-baseline gap-3 no-wrap"):
                    ui.label("→").classes("mono faint")
                    ui.label(q).classes("text-[15px]").style("color: var(--paper-80)")
        shown["queries"] = len(current["queries"])
        sources_title.set_text(f"Источники · {len(current['urls'])}")
        with sources_box:
            for url, score in current["urls"][shown["urls"]:]:
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    ui.label(score or "–").classes(f"score score-{(score or '-')[0]}")
                    ui.link(domain(url), url, new_tab=True).classes("plain text-[14px] font-medium whitespace-nowrap")
                    ui.label(url).classes("url truncate")
        shown["urls"] = len(current["urls"])
        if current["log"] != shown["log"]:  # the worker sends the last 200 lines
            log.clear()
            for line in current["log"]:
                log.push(line)
            shown["log"] = current["log"]

        if current["status"] == "done":
            timer.deactivate()
            ui.navigate.to(f"/report/{current['report_id']}")
        elif current["status"] in ("error", "interrupted"):
            timer.deactivate()
            spinner.set_visibility(False)
            interrupted = current["status"] == "interrupted"
            with error_box, ui.card().classes("w-full px-5 py-4").style(
                    "background: rgba(122,42,53,.22) !important; border-color: var(--bordo) !important"):
                ui.label("Исследование прервано: сервер перезапускался." if interrupted else current["error"] or "Ошибка") \
                    .classes("text-[15px]")
                primary_button("Перезапустить" if interrupted else "Попробовать снова", again, icon="refresh").classes("mt-2 self-start")

    timer = ui.timer(1.0, update, immediate=False)
    update()
    return None


@ui.page("/report/{report_id}")
def report_page(report_id: int) -> None:
    frame("Отчёт — DEEper")
    library = Library()
    entry = library.get(report_id)
    if not entry or not entry.md_path.exists():
        with ui.column().classes("w-full max-w-3xl mx-auto mt-16 px-6"):
            ui.label("Отчёт не найден.").classes("muted")
        return
    markdown = entry.md_path.read_text("utf-8")
    meta = json.loads(entry.base.with_suffix(".json").read_text("utf-8"))
    qa = load_qa(entry.md_path)
    blocked = set(blocklist.blocked_domains())

    def delete() -> None:
        library.delete(entry.id)
        ui.navigate.to("/")

    with ui.dialog() as confirm, ui.card().classes("p-6 gap-4 w-[420px]"):
        ui.label("Удалить отчёт вместе с файлами?").classes("text-lg font-semibold")
        ui.label(entry.query).classes("muted")
        with ui.row().classes("self-end gap-2"):
            ui.button("Отмена", on_click=confirm.close).props("flat no-caps color=paper").classes("opacity-70")
            primary_button("Удалить", delete)

    with ui.column().classes("w-full max-w-4xl mx-auto mt-8 gap-3 px-6 pb-20"):
        with ui.row().classes("items-center w-full gap-2"):
            chip(f"{datetime.fromisoformat(entry.created):%d.%m.%Y %H:%M}")
            chip(MODES.get(entry.mode, entry.mode).lower())
            chip(f"{number(entry.words)} {plural(entry.words, 'слово', 'слова', 'слов')}")
            chip(f"{entry.sources_count} {plural(entry.sources_count, 'источник', 'источника', 'источников')}")
            chip(mmss(entry.duration_sec))
            if meta.get("writer"):
                chip(f"текст: {WRITER_OF_MODEL.get(meta['writer'], meta['writer'])}")
            if meta.get("cost"):
                chip(rub_text(meta["cost"].get("rub")))
            if checked := (meta.get("factcheck") or {}).get("checked"):
                doubtful = len(meta["factcheck"].get("doubtful") or [])
                chip(f"факты: {checked - doubtful} из {checked}") \
                    .tooltip("утверждения со ссылками, которые подтверждает источник (после исправлений)")
            ui.space()
            for kind in ("pdf", "docx", "md"):
                if entry.base.with_suffix(f".{kind}").exists():
                    ui.button(kind.upper(), on_click=lambda k=kind: ui.download.from_url(f"/files/{entry.id}/{k}")) \
                        .props("flat dense no-caps color=paper").classes("mono text-[12px] opacity-80 px-2")
            ui.button(icon="delete_outline", on_click=confirm.open) \
                .props('flat round dense color=paper aria-label="Удалить отчёт"').classes("opacity-50").tooltip("Удалить отчёт")
        origin = library.by_name(meta["parent"]) if meta.get("parent") else None
        if origin:
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon("subdirectory_arrow_right").classes("faint")
                ui.link(f"продолжение отчёта #{origin.id}: {origin.query}", f"/report/{origin.id}") \
                    .classes("plain mono text-[12px] muted")

        async def propose(button) -> None:
            button.props("loading")
            try:
                found = await service.followups(entry)
            except Exception as e:
                ui.notify(f"Не получилось подобрать темы: {e}", type="negative")
                return
            finally:
                button.props(remove="loading")
            if not found:
                ui.notify("Не получилось подобрать темы, попробуйте ещё раз", type="warning")
                return
            meta.update(json.loads(entry.base.with_suffix(".json").read_text("utf-8")))
            next_steps.refresh()

        @ui.refreshable
        def next_steps() -> None:
            topics = meta.get("followups") or []
            children = library.children(entry)
            related = library.related(entry, exclude={c.id for c in children} | ({origin.id} if origin else set()))
            with ui.column().classes("w-full max-w-[820px] gap-3 mt-12 pt-8").style("border-top: 1px solid var(--line)"):
                label("Что исследовать дальше")
                if topics:
                    with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-3 w-full"):
                        for t in topics:
                            with ui.link(target=follow_up_url(t["query"], t["mode"], entry.id)).classes("next"):
                                ui.label(f"{KINDS[t['kind']]} · {MODES.get(t['mode'], t['mode']).lower()}").classes("kind")
                                ui.label(t["query"]).classes("text-[15.5px] leading-snug mt-1")
                                if t["why"]:
                                    ui.label(t["why"]).classes("text-[13px] muted mt-1")
                    if meta.get("followups_cost_rub") is not None:
                        ui.label(f"подбор тем: {rub_text(meta['followups_cost_rub'])}").classes("mono text-[11px] faint")
                else:
                    with ui.row().classes("items-center gap-3"):
                        propose_button = ui.button("Предложить темы", icon="auto_awesome",
                                                   on_click=lambda: propose(propose_button)).props("outline no-caps color=paper")
                        ui.label("модель прочтёт оглавление и выводы · ≈ 0,1 ₽").classes("mono text-[11px] faint")
                for title, items in (("Продолжения этого отчёта", children), ("Похожие в библиотеке", related)):
                    if items:
                        ui.label(title).classes("brand-label mt-4")
                    for e in items:
                        with ui.row().classes("items-baseline gap-3 no-wrap"):
                            ui.label(f"#{e.id}").classes("mono text-[12px] faint")
                            ui.link(e.query, f"/report/{e.id}").classes("plain text-[15px]")

        with ui.tabs().props("no-caps dense align=left").classes("w-full") as tabs:
            ui.tab("report", label="Отчёт")
            ui.tab("sources", label=f"Источники · {len(meta['sources'])}")
            qa_tab = ui.tab("qa", label=f"Вопросы · {len(qa)}")
        ui.element("div").classes("w-full").style("border-top: 1px solid var(--line); margin-top: -13px")

        with ui.tab_panels(tabs, value="report").classes("w-full").style("background: transparent"):
            with ui.tab_panel("report").classes("px-0 pt-6"):
                ui.markdown(mark_doubtful(body(markdown), (meta.get("factcheck") or {}).get("doubtful") or [])) \
                    .classes("report")
                if checks := appended(markdown, FACTCHECK_TITLE):
                    with ui.column().classes("w-full max-w-[820px] gap-2 mt-12 pt-8").style("border-top: 1px solid var(--line)"):
                        ui.html('<a id="factcheck"></a>', sanitize=False)
                        label("Проверка фактов")
                        ui.markdown(checks).classes("report compact")
                next_steps()

            with ui.tab_panel("sources").classes("px-0 pt-4 gap-1"):
                def block(d: str, button) -> None:
                    blocklist.block(d)
                    button.disable()
                    ui.notify(f"{d} больше не будет источником", type="info")

                for url in meta["sources"]:
                    d = domain(url)
                    with ui.row().classes("items-center w-full gap-3 no-wrap py-1"):
                        ui.link(d, url, new_tab=True).classes("plain text-[14px] font-medium whitespace-nowrap")
                        ui.label(url).classes("url truncate flex-grow")
                        b = ui.button(icon="block").props(f'flat round dense size=sm color=paper aria-label="Не использовать {d}"') \
                            .classes("opacity-40").tooltip(f"Не использовать {d}")
                        b.on_click(lambda d=d, b=b: block(d, b))
                        if d in blocked:
                            b.disable()

            with ui.tab_panel("qa").classes("px-0 pt-4"):
                qa_box = ui.column().classes("w-full gap-4")

                def show(item: dict) -> None:
                    with qa_box, ui.card().classes("w-full px-6 py-5 gap-2"):
                        label("Вопрос")
                        ui.label(item["q"]).classes("text-[16px] font-semibold")
                        ui.markdown(item["a"]).classes("report compact")
                        if item.get("research"):
                            with ui.link(target=follow_up_url(item["research"], "quick", entry.id)).classes("next mt-1"):
                                ui.label("Исследовать отдельно").classes("kind")
                                ui.label(item["research"]).classes("text-[15px] leading-snug mt-1")
                        if item.get("cost_rub") is not None:
                            ui.label(rub_text(item["cost_rub"])).classes("mono text-[11px] faint")

                for item in qa:
                    show(item)

                async def submit() -> None:
                    q = (question.value or "").strip()
                    if not q:
                        return
                    send.props("loading")
                    question.disable()
                    try:
                        item = await service.ask(entry, q)
                    except Exception as e:
                        ui.notify(f"Ошибка: {e}", type="negative")
                        return
                    finally:
                        send.props(remove="loading")
                        question.enable()
                    show(item)
                    question.value = ""
                    qa_tab.props(f'label="Вопросы · {len(load_qa(entry.md_path))}"')

                with ui.row().classes("w-full items-end no-wrap gap-3 mt-2"):
                    question = ui.textarea(placeholder="Вопрос по отчёту — ответ строится только по его тексту") \
                        .props("outlined autogrow dark").classes("flex-grow")
                    question.on("keydown.meta.enter", submit)
                    question.on("keydown.ctrl.enter", submit)
                    send = ui.button(icon="arrow_upward", on_click=submit) \
                        .props('unelevated round color=bordo text-color=paper aria-label="Спросить"')


@app.get("/files/{report_id}/{kind}")
async def report_file(report_id: int, kind: str):
    entry = Library().get(report_id)
    path = entry.base.with_suffix(f".{kind}") if entry and kind in ("pdf", "docx", "md") else None
    if not path or not path.exists():
        return Response(status_code=404)
    return FileResponse(path, filename=path.name)


async def on_startup() -> None:
    Library().sync()


app.on_startup(on_startup)


def main() -> None:
    configure_logging()
    host, port = ("0.0.0.0" if PUBLIC else "127.0.0.1"), PORT
    logging.info("Веб-интерфейс: http://%s:%s, пароль %s", host, port, "задан" if PASSWORD else ("НЕ задан" if PUBLIC else "не нужен"))
    ui.run(
        host=host,
        port=port,
        title="DEEper",
        favicon=ROOT / "static" / "favicon.png",
        dark=True,
        language="ru",
        storage_secret=storage_secret(),
        reload=False,
        show=False,
        show_welcome_message=not PUBLIC,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
