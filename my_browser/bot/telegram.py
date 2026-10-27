"""Telegram bot: send a topic, pick a mode, get the report as PDF, DOCX and Markdown; /news for the news radar.

It talks to core's API (CORE_URL with the service token) and, for news, to the news part's (NEWS_URL, in-process
when unset). The all-in-one app.py starts it; on its own: uv run python -m my_browser.bot (needs
TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS).
"""

import asyncio
import html
import logging
import os
import re
import time
from contextlib import suppress

import httpx
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    LinkPreviewOptions,
    Message,
)

from my_browser.common.clients import call
from my_browser.common.report import ANY_ANSWER, KINDS
from my_browser.common.settings import DEFAULT_MODE, DEFAULT_SOURCES, FACTCHECK_MODES
from my_browser.common.text import CITATIONS, plural, rub_text

MODE_BUTTONS = {"quick": "⚡ Обзор", "detailed": "📄 Подробный", "deep": "🔬 Глубокий"}
SOURCE_BUTTONS = {"web": "🌐 Веб", "academic": "🎓 Наука", "all": "🌐+🎓 Всё"}
FACTCHECK_BUTTONS = {None: "🔎 Проверка фактов: как в режиме", True: "🔎 Проверка фактов: да", False: "🔎 Проверка фактов: нет"}
TG_LIMIT = 4000
TICK_SEC = 5  # status refresh; also proves the bot is alive during long runs
NOTIFY_SEC = 60  # how often to look for researches interrupted by a restart
NEWS_SEC = 30  # how often to take the news part's urgent stories and digests
HOLD_SEC = 5  # a follow-up started by one tap waits this long for «Отменить» (core/jobs.HOLD_SEC)

HELP = """<b>Глубокие исследования</b>

Пришлите тему — выберите режим и источники кнопками:
⚡ Обзор — отчёт на 5–7 минут чтения: главное, 3–4 раздела, чего пока не знаем (~2 мин, текст пишет Pro)
📄 Подробный — разделы по подтемам, до 5000 слов, около 20 минут чтения (~5–7 мин)
🔬 Глубокий — рекурсивный поиск вширь и вглубь (дольше всех)
❓ Уточнить тему — 2–3 вопроса с вариантами, если тема широкая
🔎 Проверка фактов — сверить утверждения со ссылками с источниками и исправить расхождения (+1–2 мин); «как в режиме» — во всех режимах да

Отчёт придёт файлами PDF, DOCX и Markdown, а главное — сообщением.
Следом — что исследовать дальше: нажмите номер, и обзор начнётся сразу (5 секунд можно отменить). У первого отчёта ветки бывает маршрут из нескольких шагов: ➡️ ведёт по нему.
Ответьте на сообщение с номером отчёта, чтобы задать по нему вопрос.

/history — последние отчёты
/view N — прислать отчёт N ещё раз
/search текст — поиск по всем отчётам
/ask N вопрос — вопрос по отчёту N
/delete N — удалить отчёт N
/block домен — не использовать сайт как источник (/unblock, /blocked)

<b>Новости об ИИ</b> приходят сами: срочное — сразу (ночью ждёт утра), дайджест — в 09:00 и 18:00. Кнопка 🔎 под сюжетом запускает по нему исследование.
/news — статус: источники, сбор, расход
/sources — источники и их состояние
/add ссылка на RSS или @канал — добавить источник; /remove имя — выключить
/time 09:00 18:00 — время дайджестов (/time off — без них)
/quiet 23:00 08:00 — тихие часы (/quiet off)
/urgent 8 — порог срочного, от 1 до 10 (/urgent off)
/topics — темы кнопками
/dropped — что отсеяно за сутки и почему; «↩️» возвращает сюжет
/pause, /resume — остановить и продолжить новости

/profile — что о вас знать продолжениям «Для меня»; /profile текст — заменить

<b>Возможности</b> (хакатоны, школы, стажировки, гранты с дедлайнами) переехали в отдельное приложение OnePath."""

dp = Dispatcher()
pending: dict[str, dict] = {}  # topic awaiting a mode choice, keyed by the message that offered it
background: set[asyncio.Task] = set()


async def core(method: str, path: str, **kwargs):
    """JSON from core's API; httpx.HTTPStatusError on 4xx/5xx."""
    return (await call("core", method, path, **kwargs)).json()


async def report_view(report_id: int) -> dict | None:
    try:
        return await core("GET", f"/api/reports/{report_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


class AllowedUsers(BaseMiddleware):
    """Personal bot: everyone else only learns their ID (to add it to .env) and nothing runs."""

    def __init__(self, allowed: set[int]):
        self.allowed = allowed

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id in self.allowed:
            return await handler(event, data)
        if isinstance(event, Message) and user:
            await event.answer(
                f"Доступ закрыт. Ваш Telegram ID: <code>{user.id}</code>\n"
                "Добавьте его в TELEGRAM_ALLOWED_USERS в .env и перезапустите бота."
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("Доступ закрыт", show_alert=True)


class Status:
    """The status message of one research run, re-rendered from the job's state in core."""

    def __init__(self, message: Message, header: str):
        self.message = message
        self.header = header
        self._shown = ""
        self._shown_at = 0.0

    async def follow(self, job_id: str) -> dict:
        """The job once it is no longer active; {"status": "cancelled"} when it was cancelled before it started.
        While it waits, the message has «✖️ Отменить»."""
        while True:
            await asyncio.sleep(TICK_SEC)
            try:
                job = await core("GET", f"/api/jobs/{job_id}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:  # cancelled: the queue drops it
                    return {"status": "cancelled"}
                raise
            if not job["active"]:
                return job
            queued = job["status"] == "queued"
            wait = max(0, round((job.get("not_before") or 0) - time.time())) if queued else 0
            text = (f"Начну через {wait} с" if wait else "В очереди: дождусь окончания текущего исследования...") if queued else job["phase"]
            if text != self._shown or time.monotonic() - self._shown_at >= 15:
                await self.render(f"⏳ {html.escape(text or '')}\n⏱ {_mmss(job['elapsed'])}", _cancel_keyboard(job_id) if queued else None)
                self._shown, self._shown_at = text, time.monotonic()

    async def render(self, body: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
        with suppress(TelegramBadRequest):
            await self.message.edit_text(f"{self.header}\n\n{body}", reply_markup=keyboard)


def _mmss(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _chunks(text: str, limit: int = TG_LIMIT) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts + [text] if text else parts


def _summary(markdown: str) -> tuple[str, str] | None:
    """(what it is, its text): the overview's «Главное», else a longer report's conclusion; without the citations in
    brackets (the files have them), other links as their anchors."""
    for name, heading in (("главное", r"Главное|Key points"), ("заключение", r"Заключение|Выводы|Conclusion")):
        match = re.search(rf"^##\s+(?:{heading})[^\n]*\n(.*?)(?=^##\s|\Z)", markdown, re.S | re.M | re.I)
        if match and (text := re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", CITATIONS.sub("", match.group(1))).strip()):
            return name, text
    return None


def _factcheck_line(check: dict | None) -> str | None:
    if not check or not check.get("checked"):
        return None
    fixed, doubtful = check["relinked"] + check["rewritten"], len(check["doubtful"])
    return (f"🔎 Проверка фактов: источники подтверждают {check['checked'] - doubtful} из {check['checked']} утверждений со ссылками"
            + (f" (исправлено {fixed})" if fixed else "")
            + (f"; без подтверждения {doubtful} — список в конце отчёта" if doubtful else "") + ".")


def _cost(meta: dict) -> str:
    return rub_text((meta.get("cost") or {}).get("rub")) if meta.get("cost") else "—"


def _header(job: dict) -> str:
    return (f"🔎 <b>{html.escape(job['query'])}</b>\n{MODE_BUTTONS[job['mode']]} · {SOURCE_BUTTONS.get(job['sources'], job['sources'])}"
            + (" · проверка фактов" if job.get("factcheck") else ""))


def _restart_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔁 Перезапустить", callback_data=f"x:{job_id}")]])


def _cancel_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отменить", callback_data=f"cx:{job_id}")]])


def _topic_text(item: dict, note: str = "") -> str:
    lines = [f"🔎 <b>{html.escape(item['query'])}</b>"]
    if item.get("parent_id"):
        lines.append(f"↳ продолжение отчёта #{item['parent_id']}")
    if item.get("recommended"):
        lines.append(f"рекомендую: {MODE_BUTTONS[item['recommended']]}")
    if note:
        lines.append(f"<i>{html.escape(note)}</i>")
    return "\n".join(lines) + "\n\nВыберите режим. Второй ряд — источники, третий — проверка фактов."


def _topic_keyboard(key: str) -> InlineKeyboardMarkup:
    item = pending[key]
    modes = [
        InlineKeyboardButton(text=("★ " if mode == item.get("recommended") else "") + label, callback_data=f"m:{key}:{mode}")
        for mode, label in MODE_BUTTONS.items()
    ]
    srcs = [
        InlineKeyboardButton(text=("✓ " if src == item["sources"] else "") + label, callback_data=f"s:{key}:{src}")
        for src, label in SOURCE_BUTTONS.items()
    ]
    rows = [modes, srcs, [InlineKeyboardButton(text=FACTCHECK_BUTTONS[item.get("factcheck")], callback_data=f"k:{key}")]]
    if not item.get("clarified"):
        rows.append([InlineKeyboardButton(text="❓ Уточнить тему", callback_data=f"c:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _offer(send, key: str, query: str, parent_id: int | None = None, recommended: str | None = None) -> None:
    """Ask for the mode and sources of a new research; `send` is message.reply or message.answer."""
    pending[key] = {"query": query, "sources": DEFAULT_SOURCES, "recommended": recommended, "parent_id": parent_id,
                    "factcheck": None}  # None: as the mode has it
    await send(_topic_text(pending[key]), reply_markup=_topic_keyboard(key))


async def send_report(message: Message, view: dict) -> None:
    entry, meta = view["entry"], view["meta"]
    caption = (
        f"📄 <b>Отчёт #{entry['id']}</b>: {html.escape(entry['query'])}\n"
        f"≈ {entry.get('reading_min') or '?'} мин чтения · {entry['words']} слов · {entry['sources_count']} источников · "
        f"{_mmss(entry['duration_sec'])} · {_cost(meta)}"
    )
    files = []
    for kind in ("pdf", "docx", "md"):
        with suppress(httpx.HTTPStatusError):
            response = await call("core", "GET", f"/api/reports/{entry['id']}/files/{kind}")
            files.append(BufferedInputFile(response.content, filename=f"{entry['name']}.{kind}"))
    if not files:
        await message.answer(f"Файлы отчёта #{entry['id']} не найдены.")
        return
    if len(files) == 1:  # a media group needs 2-10 items
        await message.answer_document(files[0], caption=caption)
    else:
        await message.answer_media_group([
            InputMediaDocument(media=f, caption=caption if f is files[-1] else None) for f in files
        ])

    if found := _summary(view["markdown"]):
        for part in _chunks(f"📌 Отчёт #{entry['id']} — {found[0]}\n\n{found[1]}"):
            await message.answer(part, parse_mode=None)
    if line := _factcheck_line(meta.get("factcheck")):
        await message.answer(line)
    await message.answer(f"Ответьте на сообщение с «#{entry['id']}», чтобы задать вопрос по этому отчёту.")

    topics, route = meta.get("followups") or [], view.get("route")
    step = route["steps"][route["next"] - 1] if route and route.get("next") else None
    if topics or step:
        lines, buttons = [f"🧭 <b>Что исследовать дальше</b> — по отчёту #{entry['id']}"], []
        if step:
            of = f"{route['next']}/{len(route['steps'])}"
            lines.append(f"➡️ <b>Дальше по маршруту, шаг {of}:</b> {html.escape(step['query'])}"
                         + (f"\n<i>{html.escape(step['why'])}</i>" if step.get("why") else ""))
            buttons.append(InlineKeyboardButton(text=f"➡️ {of}", callback_data=f"rt:{entry['id']}:{route['next']}"))
        for i, t in enumerate(topics, 1):
            lines.append(f"{i}. <b>{KINDS.get(t['kind'], t['kind'])}:</b> {html.escape(t['query'])}"
                         + (f"\n<i>{html.escape(t['why'])}</i>" if t["why"] else ""))
        buttons += [InlineKeyboardButton(text=str(i), callback_data=f"f:{entry['id']}:{i - 1}") for i in range(1, len(topics) + 1)]
        await message.answer("\n\n".join(lines) + "\n\nНажмите кнопку — обзор на 5–7 минут начнётся сразу (5 секунд можно отменить).",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))


async def finish(status: Status, job: dict) -> None:
    """Render a finished, failed or interrupted job; send the report when it is ready."""
    if job["status"] == "cancelled":  # on_cancel has said so in the message
        return
    if job["status"] == "error":
        await status.render(f"❌ {html.escape(job['error'] or 'Ошибка')}", _restart_keyboard(job["id"]))
        return
    if job["status"] == "interrupted":
        await status.render("⚠️ Исследование прервано: сервер перезапускался.", _restart_keyboard(job["id"]))
        await core("POST", f"/api/jobs/{job['id']}/notified")
        return
    view = await report_view(job["report_id"])
    entry = view["entry"]
    await status.render(f"✅ Готово за {_mmss(job['elapsed'])}: ≈ {entry.get('reading_min') or '?'} мин чтения ({entry['words']} слов), "
                        f"{entry['sources_count']} источников, {_cost(view['meta'])}")
    await send_report(status.message, view)


async def run_research(status: Status, item: dict, mode: str) -> None:
    job = await core("POST", "/api/jobs", json={
        "query": item["query"], "mode": mode, "sources": item["sources"], "parent_id": item.get("parent_id"),
        "origin": "telegram", "origin_ref": {"chat": status.message.chat.id, "message": status.message.message_id},
        "factcheck": item.get("factcheck"),
    })
    await finish(status, await status.follow(job["id"]))


async def answer_question(message: Message, report_id: int, question: str) -> None:
    if not await report_view(report_id):
        await message.reply(f"Отчёт #{report_id} не найден. Список: /history")
        return
    thinking = await message.reply(f"🤔 Ищу ответ в отчёте #{report_id}...")
    try:
        item = await core("POST", f"/api/reports/{report_id}/ask", json={"question": question}, timeout=300)
    except Exception as e:
        logging.exception("Ask failed")
        await thinking.edit_text(f"❌ Ошибка: {html.escape(str(e)[:300])}")
        return
    await thinking.delete()
    for part in _chunks(f"💬 По отчёту #{report_id}:\n\n{item['a']}"):
        await message.reply(part, parse_mode=None)
    if item.get("research"):
        index = len((await report_view(report_id))["qa"]) - 1
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔎 Исследовать отдельно", callback_data=f"r:{report_id}:{index}"),
        ]])
        await message.answer(f"Тема для отдельного исследования:\n<b>{html.escape(item['research'])}</b>", reply_markup=keyboard)


@dp.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@dp.message(Command("history"))
async def cmd_history(message: Message) -> None:
    entries = await core("GET", "/api/reports", params={"limit": 15})
    if not entries:
        await message.answer("Отчётов пока нет. Пришлите тему.")
        return
    lines = [
        f"<b>#{e['id']}</b> · {e['created'][:10]} · {MODE_BUTTONS.get(e['mode'], e['mode'])} · {e['words']} слов\n{html.escape(e['query'])}"
        for e in entries
    ]
    for part in _chunks("\n\n".join(lines)):
        await message.answer(part)


@dp.message(Command("view"))
async def cmd_view(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Укажите номер: /view 3")
        return
    view = await report_view(int(command.args))
    if not view:
        await message.answer(f"Отчёт #{command.args.strip()} не найден. Список: /history")
        return
    await send_report(message, view)


@dp.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Укажите, что искать: /search натрий-ионные")
        return
    results = await core("GET", "/api/reports", params={"q": command.args, "limit": 5})
    if not results:
        await message.answer("Ничего не нашлось.")
        return
    lines = [f"<b>#{e['id']}</b> {html.escape(e['query'])}\n<i>{html.escape(e['snippet'])}</i>" for e in results]
    await message.answer("\n\n".join(lines) + "\n\nОткрыть: /view N")


@dp.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject) -> None:
    match = re.match(r"\s*#?(\d+)\s+(.+)", command.args or "", re.S)
    if not match:
        await message.answer("Формат: /ask 3 Какие главные риски?")
        return
    await answer_question(message, int(match.group(1)), match.group(2))


@dp.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Укажите номер: /delete 3")
        return
    view = await report_view(int(command.args))
    if not view:
        await message.answer(f"Отчёт #{command.args.strip()} не найден.")
        return
    entry = view["entry"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{entry['id']}"),
        InlineKeyboardButton(text="Отмена", callback_data="cancel"),
    ]])
    await message.answer(f"Удалить отчёт #{entry['id']} «{html.escape(entry['query'])}» вместе с файлами?", reply_markup=keyboard)


@dp.message(Command("block"))
async def cmd_block(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /block example.com")
        return
    domain = (await core("POST", "/api/blocklist", json={"domain": command.args.strip()}))["domain"]
    await message.answer(f"🚫 {html.escape(domain)} больше не будет источником. Вернуть: /unblock {html.escape(domain)}")


@dp.message(Command("unblock"))
async def cmd_unblock(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /unblock example.com")
        return
    found = (await core("DELETE", f"/api/blocklist/{command.args.strip()}"))["removed"]
    await message.answer("✅ Разблокирован." if found else "Этого домена нет в списке. Список: /blocked")


@dp.message(Command("blocked"))
async def cmd_blocked(message: Message) -> None:
    domains = (await core("GET", "/api/blocklist"))["domains"]
    await message.answer(
        "Заблокированные источники:\n" + "\n".join(html.escape(d) for d in domains) if domains
        else "Свой список пуст. Соцсети, видео и магазины отсеиваются всегда, остальное оценивает модель."
    )


@dp.message(Command("news"))
async def cmd_news(message: Message) -> None:
    try:
        status = (await call("news", "GET", "/status")).json()
    except Exception as e:  # the news part runs elsewhere (NEWS_URL) and does not answer, or is not installed here
        logging.warning("News status unavailable: %r", e)
        await message.answer("Новости сейчас недоступны.")
        return
    await message.answer(news_text(status))


def news_text(status: dict) -> str:
    """/news: the news part's sources and their health, the last day, LLM spend and the schedule."""
    sources, settings, spend = status["sources"], status["settings"], status["spend"]
    lines = ["📡 <b>Новости об ИИ</b>"]
    if sources["pending"] == sources["enabled"]:
        lines.append(f"Источники: {sources['enabled']}, ещё не опрашивались")
    else:
        lines.append(f"Источники: {sources['enabled']}, работают {sources['ok']}")
    if failing := sources["failing"]:
        names = ", ".join(html.escape(f["name"]) for f in failing[:3])
        more = f" и ещё {len(failing) - 3}" if len(failing) > 3 else ""
        lines.append(f"⚠️ С ошибкой {len(failing)}: {names}{more}")
    posts, stories = status["items"]["day"], status["stories"]["day"]
    lines.append(f"За сутки: {posts} {plural(posts, 'пост', 'поста', 'постов')}, "
                 + (f"{stories} {plural(stories, 'сюжет', 'сюжета', 'сюжетов')}" if stories else "сюжетов пока нет"))
    lines.append(f"LLM: сегодня {rub_text(spend['today_rub'])}, за месяц {rub_text(spend['month_rub'])} "
                 f"из {spend['budget_month_rub']:g}\u00a0₽")
    digest = ", ".join(settings["digest_times"]) or "выключен"
    urgent = f"срочное от {settings['urgent_min_importance']}/10" if settings["urgent_enabled"] else "срочное выключено"
    quiet = (f"тихие часы {settings['quiet_start']}–{settings['quiet_end']}"
             if settings["quiet_start"] != settings["quiet_end"] else "без тихих часов")
    lines.append(f"Дайджест: {digest} ({html.escape(settings['timezone'])}), {urgent}, {quiet}")
    if last := status.get("last_collect"):
        lines.append(f"Последний сбор: {last['at'][11:16]}, новых постов {last['new']}")
    if settings["paused"]:
        lines.append("⏸ На паузе")
    elif not status["loop_alive"]:
        lines.append("⏸ Цикл новостей не запущен")
    return "\n".join(lines)


async def _news_settings(message: Message, changes: dict, done: str) -> None:
    """Change the news part's settings; its refusal (in Russian) goes back to the user as it is."""
    try:
        await call("news", "PUT", "/settings", json=changes)
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail") if e.response.status_code == 422 else None
        await message.answer(f"Не получилось: {html.escape(detail or str(e)[:200])}")
        return
    await message.answer(done)


TIME = re.compile(r"^\d{1,2}:\d{2}$")


def _times(args: str) -> list[str]:
    """«9:00 18:00» -> ["09:00", "18:00"]; the news part checks the rest."""
    return [t.zfill(5) if TIME.match(t) else t for t in args.replace(",", " ").split()]


@dp.message(Command("time"))
async def cmd_time(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer("Формат: /time 09:00 18:00 — до шести дайджестов в день; /time off — без дайджестов")
        return
    times = [] if args.lower() in ("off", "нет", "выкл") else _times(args)
    await _news_settings(message, {"digest_times": times},
                         f"⏰ Дайджест: {', '.join(times)}" if times else "⏰ Дайджестов не будет, только срочное")


@dp.message(Command("quiet"))
async def cmd_quiet(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if args.lower() in ("off", "нет", "выкл"):
        await _news_settings(message, {"quiet_start": "00:00", "quiet_end": "00:00"}, "🔔 Тихих часов нет: срочное приходит в любое время")
        return
    times = _times(args)
    if len(times) != 2:
        await message.answer("Формат: /quiet 23:00 08:00 — срочное в это время ждёт утра; /quiet off — без тихих часов")
        return
    await _news_settings(message, {"quiet_start": times[0], "quiet_end": times[1]}, f"🌙 Тихие часы: {times[0]}–{times[1]}")


@dp.message(Command("urgent"))
async def cmd_urgent(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if args.lower() in ("off", "нет", "выкл"):
        await _news_settings(message, {"urgent_enabled": False}, "⚡️ Срочное выключено: всё только в дайджестах")
        return
    if not args.isdigit():
        await message.answer("Формат: /urgent 8 — присылать сразу сюжеты от этой важности (1–10); /urgent off — не присылать")
        return
    await _news_settings(message, {"urgent_enabled": True, "urgent_min_importance": int(args)},
                         f"⚡️ Срочное: от важности {args} из 10")


@dp.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    await _news_settings(message, {"paused": True}, "⏸ Новости на паузе: сбор идёт, оценки и сообщения — нет. Продолжить: /resume")


@dp.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    await _news_settings(message, {"paused": False}, "▶️ Новости снова идут")


def _topics_keyboard(settings: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ " if key in settings["topics"] else "▫️ ") + name, callback_data=f"nt:{key}")]
        for key, name in settings["topic_names"].items()])


@dp.message(Command("topics"))
async def cmd_topics(message: Message) -> None:
    settings = (await call("news", "GET", "/settings")).json()
    await message.answer("Темы новостей: нажмите, чтобы включить или выключить.", reply_markup=_topics_keyboard(settings))


@dp.callback_query(F.data.startswith("nt:"))
async def on_topic_toggle(callback: CallbackQuery) -> None:
    topic = callback.data.split(":", 1)[1]
    settings = (await call("news", "GET", "/settings")).json()
    topics = [t for t in settings["topics"] if t != topic] if topic in settings["topics"] else settings["topics"] + [topic]
    try:
        await call("news", "PUT", "/settings", json={"topics": topics})
    except httpx.HTTPStatusError:
        await callback.answer("Не получилось", show_alert=True)
        return
    await callback.answer()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=_topics_keyboard({**settings, "topics": topics}))


SOURCE_MARK = {True: "✅", False: "⚠️"}


@dp.message(Command("profile"))
async def cmd_profile(message: Message, command: CommandObject) -> None:
    """/profile shows what the research follow-ups «Для меня» know about the user; /profile <text> replaces it."""
    text = (command.args or "").strip()
    if not text:
        profile = (await call("news", "GET", "/settings")).json()["profile"]
        await message.answer("👤 <b>Профиль</b> — его знают продолжения «Для меня» под отчётами\n\n"
                             + (html.escape(profile) if profile else "Пока не задан.") + "\n\n"
                             "Заменить: /profile и текст о себе — учёба, интересы, цели. Контакты, email и ники "
                             "в запросы не уходят.")
        return
    await _news_settings(message, {"profile": text}, "👤 Профиль сохранён: продолжения «Для меня» будут учитывать его")


@dp.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    rows = (await call("news", "GET", "/sources")).json()
    lines = []
    for row in rows:
        mark = "⏸" if not row["enabled"] else "🕓" if row["polled"] is None else SOURCE_MARK[not row["last_error"]]
        note = f" — {html.escape(row['last_error'])}" if row["enabled"] and row["last_error"] else f" — {row['week']} за неделю"
        lines.append(f"{mark} <b>#{row['id']}</b> {html.escape(row['name'])}{note}")
    working = sum(1 for r in rows if r["enabled"] and r["last_ok"] and not r["last_error"])
    head = f"📡 <b>Источники новостей</b>: {sum(1 for r in rows if r['enabled'])}, работают {working}\n"
    legend = "✅ работает · ⚠️ ошибка · 🕓 ещё не опрошен · ⏸ выключен\n\n"
    for part in _chunks(legend + head + "\n".join(lines)):
        await message.answer(part, link_preview_options=LinkPreviewOptions(is_disabled=True))


OPPS_WORDS = ("opps", "возможности")
MOVED = "Возможности переехали в отдельное приложение OnePath: источники, список и «Мои» теперь там."


@dp.message(Command("opps", "deadlines", "opptypes"))
async def cmd_moved(message: Message) -> None:
    """The opportunities' commands, until 27.10.2026: the lane moved to OnePath, an app without a bot."""
    await message.answer(MOVED)


@dp.callback_query(F.data.startswith(("op:", "ot:", "oy:")))
async def on_moved(callback: CallbackQuery) -> None:
    """The buttons of the opportunities' old messages (op:, ot:, oy:) still sit in the chat."""
    await callback.answer(MOVED, show_alert=True)


@dp.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    """/add <feed or @channel> adds a news source."""
    text = (command.args or "").strip()
    if not text:
        await message.answer("Формат: /add https://example.com/feed.xml или /add @channel")
        return
    if text.split(maxsplit=1)[0].lower() in OPPS_WORDS:  # «/add opps …» added an opportunities source until 27.10.2026
        await message.answer(MOVED)
        return
    progress = await message.answer("⏳ Проверяю источник…")
    try:
        source = (await call("news", "POST", "/sources", json={"text": text}, timeout=60)).json()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail") if e.response.status_code == 422 else str(e)[:200]
        await progress.edit_text(f"❌ {html.escape(detail)}")
        return
    await progress.edit_text(f"✅ Добавлен <b>#{source['id']}</b> {html.escape(source['name'])}: первый опрос — "
                             f"в течение минуты. Выключить: /remove {source['id']}")


@dp.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /remove 12 (номер из /sources) или /remove часть названия")
        return
    found = (await call("news", "GET", "/sources/find", params={"q": command.args})).json()
    found = [s for s in found if s["enabled"]] or found
    if len(found) != 1:
        listing = "\n".join(f"#{s['id']} {html.escape(s['name'])}" for s in found[:10])
        await message.answer("Не нашёл такого источника. Номера — в /sources" if not found
                             else f"Подходит несколько, уточните номером:\n{listing}")
        return
    source = (await call("news", "DELETE", f"/sources/{found[0]['id']}")).json()
    url = source["url"] if source["kind"] != "telegram" else f"@{source['url']}"
    await message.answer(f"⏸ Выключен #{source['id']} {html.escape(source['name'])}. Вернуть: /add {html.escape(url)}")


@dp.message(Command("dropped"))
async def cmd_dropped(message: Message) -> None:
    """What the news part dropped in the last day, grouped by why; «↩️ N» sends a story that mattered after all."""
    stories = (await call("news", "GET", "/dropped", params={"hours": 24, "limit": 30})).json()
    if not stories:
        await message.answer("За сутки ничего не отсеяно.")
        return
    off_topic = [s for s in stories if s["drop_reason"] == "не про ИИ"]
    minor = [s for s in stories if s["drop_reason"] != "не про ИИ"]
    lines, numbered = [f"🗑 <b>Отсеяно за сутки</b>: {len(stories)}"], []
    for title, group in (("Мелочи и мнения", minor), ("Не про ИИ", off_topic)):
        if not group:
            continue
        lines.append(f"\n<b>{title}</b> — {len(group)}")
        for story in group:
            numbered.append(story["id"])
            reason = (story["drop_reason"] or "").removeprefix("неважно: ")
            lines.append(f"{len(numbered)}. {html.escape(story['title_ru'] or story['title'] or '')} — <i>{html.escape(reason)}</i>")
    lines.append("\nВернуть сюжет, который был важен: кнопка с его номером. Это учтётся как 👍.")
    buttons = [InlineKeyboardButton(text=f"↩️ {n}", callback_data=f"nd:{sid}") for n, sid in enumerate(numbered[:20], 1)]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 5] for i in range(0, len(buttons), 5)])
    parts = _chunks("\n".join(lines))
    for n, part in enumerate(parts):
        await message.answer(part, reply_markup=keyboard if n == len(parts) - 1 else None)


@dp.callback_query(F.data.startswith("nd:"))
async def on_news_restore(callback: CallbackQuery) -> None:
    story_id = callback.data.split(":")[1]
    try:
        await call("news", "POST", f"/stories/{story_id}/restore")
    except httpx.HTTPStatusError:
        await callback.answer("Этот сюжет уже вернули или он не отсеян", show_alert=True)
        return
    await callback.answer("Возвращаю: сюжет придёт сообщением, а похожие станут заметнее")


@dp.callback_query(F.data.startswith("nr:"))
async def on_news_research(callback: CallbackQuery) -> None:
    """«🔎» under a news story: offer it as a research topic, as follow-up topics are offered."""
    story_id = callback.data.split(":")[1]
    try:
        story = (await call("news", "GET", f"/stories/{story_id}")).json()
    except Exception:
        await callback.answer("Сюжет не найден", show_alert=True)
        return
    await callback.answer()
    key = f"{callback.message.chat.id}_{callback.message.message_id}_n{story_id}"
    await _offer(callback.message.answer, key, story["title_ru"] or story["title"], recommended="quick")


@dp.callback_query(F.data.startswith("nf:"))
async def on_news_feedback(callback: CallbackQuery) -> None:
    _, story_id, value = callback.data.split(":")
    with suppress(Exception):
        await call("news", "POST", "/feedback", json={"story_id": int(story_id), "value": int(value)})
    await callback.answer("Учту: больше такого" if value == "1" else "Учту: меньше такого")


def _keyboard(rows: list) -> InlineKeyboardMarkup | None:
    """The news part's buttons, rows of [label, callback data]."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
                                                 for row in rows]) if rows else None


async def deliver_news(bot: Bot, chats: set[int]) -> None:
    """The news part's urgent stories and digests, sent to every allowed user as soon as the news part queues them."""
    while True:
        try:
            for item in (await call("news", "GET", "/deliveries")).json() if chats else []:
                parts = _chunks(item["text"])
                try:
                    for chat in chats:
                        for n, part in enumerate(parts):
                            await bot.send_message(chat, part, reply_markup=_keyboard(item["buttons"]) if n == len(parts) - 1 else None,
                                                   link_preview_options=LinkPreviewOptions(is_disabled=True))
                except TelegramBadRequest as e:  # a message Telegram refuses would be refused again: give it up
                    logging.error("News message %s refused by Telegram: %s", item["id"], e)
                await call("news", "POST", f"/deliveries/{item['id']}/sent")
        except Exception as e:  # the news part or Telegram is unreachable: try again on the next round
            logging.warning("News delivery: %r", e)
        await asyncio.sleep(NEWS_SEC)


@dp.callback_query(F.data.startswith("del:"))
async def on_delete(callback: CallbackQuery) -> None:
    deleted = (await core("DELETE", f"/api/reports/{int(callback.data.split(':')[1])}"))["deleted"]
    await callback.answer()
    await callback.message.edit_text(f"🗑 Отчёт #{deleted['id']} удалён." if deleted else "Уже удалён.")


@dp.callback_query(F.data == "cancel")
async def on_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Отменено.")


@dp.message(F.text & F.reply_to_message & ~F.text.startswith("/"))
async def on_reply(message: Message) -> None:
    replied = message.reply_to_message
    match = re.search(r"#(\d+)", replied.text or replied.caption or "")
    if not match:
        await message.reply("Не вижу номер отчёта в этом сообщении. Используйте /ask N вопрос")
        return
    await answer_question(message, int(match.group(1)), message.text)


@dp.message(F.text & ~F.text.startswith("/"))
async def on_topic(message: Message) -> None:
    query = message.text.strip()
    if not query:
        return
    await _offer(message.reply, f"{message.chat.id}_{message.message_id}", query)


async def start_followup(message: Message, query: str, parent_id: int, route_step: int | None = None) -> None:
    """One tap on a follow-up: an overview that continues the report starts at once, held back HOLD_SEC with «✖️ Отменить»."""
    header = (f"🔎 <b>{html.escape(query)}</b>\n{MODE_BUTTONS[DEFAULT_MODE]} · {SOURCE_BUTTONS[DEFAULT_SOURCES]}"
              + (" · проверка фактов" if DEFAULT_MODE in FACTCHECK_MODES else "")
              + f"\n↳ продолжение отчёта #{parent_id}" + (f", шаг {route_step} маршрута" if route_step else ""))
    status = Status(await message.answer(f"{header}\n\n⏳ Начинаю..."), header)
    job = await core("POST", "/api/jobs", json={
        "query": query, "mode": DEFAULT_MODE, "sources": DEFAULT_SOURCES, "parent_id": parent_id, "origin": "telegram",
        "origin_ref": {"chat": status.message.chat.id, "message": status.message.message_id},
        "route_step": route_step, "hold": HOLD_SEC})
    await status.render(f"⏳ Начну через {HOLD_SEC} с", _cancel_keyboard(job["id"]))
    await finish(status, await status.follow(job["id"]))


@dp.callback_query(F.data.startswith("f:"))
async def on_followup(callback: CallbackQuery) -> None:
    _, report_id, index = callback.data.split(":")
    view = await report_view(int(report_id))
    topics = (view["meta"].get("followups") or []) if view else []
    if int(index) >= len(topics):
        await callback.answer("Отчёт или тема не найдены", show_alert=True)
        return
    await callback.answer("Запускаю обзор")
    _spawn(start_followup(callback.message, topics[int(index)]["query"], int(report_id)))


@dp.callback_query(F.data.startswith("rt:"))
async def on_route_step(callback: CallbackQuery) -> None:
    """➡️ under a report: the next step of its branch's route."""
    _, report_id, step = callback.data.split(":")
    view = await report_view(int(report_id))
    route = (view or {}).get("route")
    if not route or not 1 <= int(step) <= len(route["steps"]):
        await callback.answer("Маршрут не найден", show_alert=True)
        return
    await callback.answer("Запускаю следующий шаг")
    _spawn(start_followup(callback.message, route["steps"][int(step) - 1]["query"], int(report_id), int(step)))


@dp.callback_query(F.data.startswith("cx:"))
async def on_cancel(callback: CallbackQuery) -> None:
    """«✖️ Отменить» under a research that has not started yet."""
    job_id = callback.data.split(":", 1)[1]
    if not (await core("POST", f"/api/jobs/{job_id}/cancel"))["cancelled"]:
        await callback.answer("Уже началось — отменить нельзя", show_alert=True)
        return
    await callback.answer("Отменено")
    with suppress(TelegramBadRequest):
        await callback.message.edit_text(f"{callback.message.html_text}\n\n✖️ Отменено", reply_markup=None)


@dp.callback_query(F.data.startswith("r:"))
async def on_research_question(callback: CallbackQuery) -> None:
    _, report_id, index = callback.data.split(":")
    view = await report_view(int(report_id))
    qa = view["qa"] if view else []
    topic = qa[int(index)].get("research") if int(index) < len(qa) else None
    if not topic:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    await callback.answer()
    key = f"{callback.message.chat.id}_{callback.message.message_id}_r"
    await _offer(callback.message.answer, key, topic, parent_id=int(report_id), recommended="quick")


@dp.callback_query(F.data.startswith("c:"))
async def on_clarify(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    item = pending.get(key)
    if not item:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    await callback.answer("Готовлю вопросы...")
    try:
        result = await core("POST", "/api/clarify", json={"query": item["query"]})
    except Exception as e:
        logging.exception("Clarify failed")
        await callback.message.answer(f"❌ Не получилось: {html.escape(str(e)[:300])}")
        return
    if not result["questions"]:
        item["clarified"] = True
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(_topic_text(item, "уточнять нечего: тема и так конкретная"),
                                             reply_markup=_topic_keyboard(key))
        return
    item["clarify"] = {"questions": result["questions"], "answers": [], "rub": result["cost_rub"]}
    await _show_question(callback.message, key)


async def _show_question(message: Message, key: str) -> None:
    state = pending[key]["clarify"]
    i = len(state["answers"])
    question = state["questions"][i]
    rows = [[InlineKeyboardButton(text=option, callback_data=f"a:{key}:{i}:{n}")] for n, option in enumerate(question["options"])]
    rows.append([InlineKeyboardButton(text=ANY_ANSWER.capitalize(), callback_data=f"a:{key}:{i}:-1")])
    with suppress(TelegramBadRequest):
        await message.edit_text(f"❓ <b>{i + 1}/{len(state['questions'])}.</b> {html.escape(question['q'])}",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("a:"))
async def on_clarify_answer(callback: CallbackQuery) -> None:
    _, key, i, n = callback.data.split(":")
    item = pending.get(key)
    state = (item or {}).get("clarify")
    if not state or int(i) != len(state["answers"]):  # stale topic or a double tap
        await callback.answer()
        return
    await callback.answer()
    question = state["questions"][int(i)]
    state["answers"].append((question["q"], question["options"][int(n)] if int(n) >= 0 else None))
    if len(state["answers"]) < len(state["questions"]):
        await _show_question(callback.message, key)
        return
    chosen = [(q, a) for q, a in state["answers"] if a]
    rub = state["rub"]
    if chosen:
        with suppress(TelegramBadRequest):
            await callback.message.edit_text("⏳ Уточняю тему...")
        try:
            result = await core("POST", "/api/refine", json={"query": item["query"], "answers": chosen})
            item["query"], rub = result["topic"], rub + result["cost_rub"]
        except Exception:
            logging.exception("Refine failed, keeping the topic as is")
    del item["clarify"]
    item["clarified"] = True
    with suppress(TelegramBadRequest):
        await callback.message.edit_text(_topic_text(item, f"тема уточнена · {rub_text(rub)}" if chosen else ""),
                                         reply_markup=_topic_keyboard(key))


@dp.callback_query(F.data.startswith("s:"))
async def on_sources(callback: CallbackQuery) -> None:
    _, key, sources = callback.data.split(":")
    if key not in pending:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    pending[key]["sources"] = sources
    await callback.answer()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=_topic_keyboard(key))


@dp.callback_query(F.data.startswith("k:"))
async def on_factcheck(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    if key not in pending:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    states = list(FACTCHECK_BUTTONS)  # as the mode has it -> yes -> no -> ...
    pending[key]["factcheck"] = states[(states.index(pending[key].get("factcheck")) + 1) % len(states)]
    await callback.answer()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=_topic_keyboard(key))


@dp.callback_query(F.data.startswith("m:"))
async def on_mode(callback: CallbackQuery) -> None:
    _, key, mode = callback.data.split(":")
    item = pending.pop(key, None)
    if not item:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    await callback.answer()
    checked = mode in FACTCHECK_MODES if item.get("factcheck") is None else item["factcheck"]
    header = (f"🔎 <b>{html.escape(item['query'])}</b>\n{MODE_BUTTONS[mode]} · {SOURCE_BUTTONS[item['sources']]}"
              + (" · проверка фактов" if checked else ""))
    if item.get("parent_id"):
        header += f"\n↳ продолжение отчёта #{item['parent_id']}"
    status = Status(callback.message, header)
    await status.render("⏳ Начинаю...")
    _spawn(run_research(status, item, mode))


@dp.callback_query(F.data.startswith("x:"))
async def on_restart(callback: CallbackQuery) -> None:
    job_id = callback.data.split(":", 1)[1]
    try:
        job = await core("POST", f"/api/jobs/{job_id}/restart")
    except httpx.HTTPStatusError:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await callback.answer()
    status = Status(callback.message, _header(job))
    await status.render("⏳ Перезапускаю...")
    _spawn(follow_and_finish(status, job["id"]))


async def follow_and_finish(status: Status, job_id: str) -> None:
    await finish(status, await status.follow(job_id))


def _spawn(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    background.add(task)
    task.add_done_callback(background.discard)


async def notify_interrupted(bot: Bot) -> None:
    """Researches started from Telegram and cut off by a restart: say so in their status message."""
    while True:
        with suppress(Exception):
            for job in await core("GET", "/api/jobs", params={"status": "interrupted", "origin": "telegram"}):
                ref = job.get("origin_ref") or {}
                if ref.get("chat"):
                    with suppress(TelegramBadRequest):
                        await bot.edit_message_text(
                            f"{_header(job)}\n\n⚠️ Исследование прервано: сервер перезапускался.",
                            chat_id=ref["chat"], message_id=ref["message"], reply_markup=_restart_keyboard(job["id"]))
                await core("POST", f"/api/jobs/{job['id']}/notified")
        await asyncio.sleep(NOTIFY_SEC)


@dp.errors()
async def on_error(event: ErrorEvent) -> None:
    logging.error("Bot handler failed: %r", event.exception, exc_info=event.exception)
    target = event.update.message or (event.update.callback_query and event.update.callback_query.message)
    if target:
        with suppress(Exception):
            await target.answer(f"❌ Ошибка: {html.escape(str(event.exception)[:300])}")


def token() -> str | None:
    # TELEGRAM_TOKEN is the name DEEper v1 used, still set on the Railway service.
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")


async def start(handle_signals: bool = True) -> None:
    """Run the bot; app.py also starts it inside the web app's event loop."""
    allowed = {int(x) for x in re.findall(r"\d+", os.environ.get("TELEGRAM_ALLOWED_USERS", ""))}
    if not allowed:
        logging.warning("TELEGRAM_ALLOWED_USERS пуст: бот только сообщит каждому его Telegram ID")
    dp.message.outer_middleware(AllowedUsers(allowed))
    dp.callback_query.outer_middleware(AllowedUsers(allowed))

    bot = Bot(token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="history", description="Последние отчёты"),
        BotCommand(command="search", description="Поиск по отчётам"),
        BotCommand(command="view", description="Прислать отчёт N"),
        BotCommand(command="ask", description="Вопрос по отчёту N"),
        BotCommand(command="delete", description="Удалить отчёт N"),
        BotCommand(command="block", description="Не брать сайт в источники"),
        BotCommand(command="blocked", description="Заблокированные сайты"),
        BotCommand(command="news", description="Новости об ИИ: статус"),
        BotCommand(command="sources", description="Источники новостей"),
        BotCommand(command="topics", description="Темы новостей"),
        BotCommand(command="dropped", description="Что отсеяно из новостей"),
        BotCommand(command="pause", description="Новости на паузу"),
        BotCommand(command="resume", description="Продолжить новости"),
        BotCommand(command="profile", description="Профиль для продолжений «Для меня»"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    me = await bot.get_me()
    logging.info("Бот @%s запущен, доступ: %s", me.username, sorted(allowed) or "никому")
    _spawn(notify_interrupted(bot))
    _spawn(deliver_news(bot, allowed))
    await dp.start_polling(bot, handle_signals=handle_signals)
