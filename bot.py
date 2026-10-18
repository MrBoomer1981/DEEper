"""Telegram bot: send a topic, pick a mode, get the report as PDF, DOCX and Markdown.

Run: uv run bot.py   (needs TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in .env)
"""

import asyncio
import html
import logging
import os
import re
import sys
import time
from contextlib import suppress

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    Message,
)

from my_browser import sources
from my_browser.ask import ask
from my_browser.library import Entry, Library
from my_browser.pipeline import DEFAULT_SOURCES, PreflightError, Report, env_overrides, overrides, preflight, research, save

MODE_BUTTONS = {"detailed": "📄 Подробный", "deep": "🔬 Глубокий", "quick": "⚡ Быстрый"}
SOURCE_BUTTONS = {"web": "🌐 Веб", "academic": "🎓 Наука", "all": "🌐+🎓 Всё"}
TG_LIMIT = 4000
TICK_SEC = 5  # status refresh; also proves the bot is alive during long runs

HELP = """<b>Глубокие исследования</b>

Пришлите тему — выберите режим и источники кнопками:
📄 Подробный — разделы по подтемам, самый длинный (~4 мин)
🔬 Глубокий — рекурсивный поиск вширь и вглубь (дольше всех)
⚡ Быстрый — один проход (~1 мин)

Отчёт придёт файлами PDF, DOCX и Markdown, а заключение — сообщением.
Ответьте на сообщение с номером отчёта, чтобы задать по нему вопрос.

/history — последние отчёты
/view N — прислать отчёт N ещё раз
/search текст — поиск по всем отчётам
/ask N вопрос — вопрос по отчёту N
/delete N — удалить отчёт N
/block домен — не использовать сайт как источник (/unblock, /blocked)"""

dp = Dispatcher()
library = Library()
research_lock = asyncio.Lock()  # one research at a time: env overrides are process-wide
pending: dict[str, dict] = {}  # topic awaiting a mode choice, keyed by the user's message
background: set[asyncio.Task] = set()


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
    """The status message of one research run, refreshed by a ticker task."""

    def __init__(self, message: Message, header: str):
        self.message = message
        self.header = header
        self.text = "Проверяю DeepSeek и поиск..."
        self.started = time.monotonic()
        self._shown = ""
        self._shown_at = 0.0

    def set(self, text: str) -> None:
        # Called from the research worker thread; rendering happens in tick().
        self.text = text

    async def tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_SEC)
            if self.text != self._shown or time.monotonic() - self._shown_at >= 15:
                await self.render(f"⏳ {html.escape(self.text)}\n⏱ {_mmss(time.monotonic() - self.started)}")
                self._shown, self._shown_at = self.text, time.monotonic()

    async def render(self, body: str) -> None:
        with suppress(TelegramBadRequest):
            await self.message.edit_text(f"{self.header}\n\n{body}")


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


def _conclusion(markdown: str) -> str | None:
    match = re.search(r"^##\s+(?:Заключение|Выводы|Conclusion)[^\n]*\n(.*?)(?=^##\s|\Z)", markdown, re.S | re.M | re.I)
    if not match:
        return None
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match.group(1)).strip() or None


def _topic_keyboard(key: str, sources: str) -> InlineKeyboardMarkup:
    modes = [InlineKeyboardButton(text=label, callback_data=f"m:{key}:{mode}") for mode, label in MODE_BUTTONS.items()]
    srcs = [
        InlineKeyboardButton(text=("✓ " if src == sources else "") + label, callback_data=f"s:{key}:{src}")
        for src, label in SOURCE_BUTTONS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[modes, srcs])


async def send_report(message: Message, entry: Entry) -> None:
    caption = (
        f"📄 <b>Отчёт #{entry.id}</b>: {html.escape(entry.query)}\n"
        f"{entry.words} слов · {entry.sources_count} источников · {_mmss(entry.duration_sec)}"
    )
    files = entry.files()
    if not files:
        await message.answer(f"Файлы отчёта #{entry.id} не найдены.")
        return
    if len(files) == 1:  # a media group needs 2-10 items
        await message.answer_document(FSInputFile(files[0]), caption=caption)
    else:
        await message.answer_media_group([
            InputMediaDocument(media=FSInputFile(p), caption=caption if p == files[-1] else None) for p in files
        ])

    conclusion = _conclusion(entry.md_path.read_text("utf-8"))
    if conclusion:
        for part in _chunks(f"📌 Отчёт #{entry.id} — заключение\n\n{conclusion}"):
            await message.answer(part, parse_mode=None)
    await message.answer(f"Ответьте на сообщение с «#{entry.id}», чтобы задать вопрос по этому отчёту.")


def _run_blocking(query: str, mode: str, sources: str, status: Status) -> Report:
    """Runs in a worker thread with its own event loop so the bot stays responsive."""
    with env_overrides(overrides(sources)):
        preflight()
        started = time.monotonic()
        markdown, urls = asyncio.run(research(query, mode, on_progress=status.set))
        status.set("Собираю PDF и DOCX...")
        return save(query, mode, markdown, urls, time.monotonic() - started)


async def run_research(status: Status, query: str, mode: str, sources: str) -> None:
    if research_lock.locked():
        await status.render("⏳ В очереди: дождусь окончания текущего исследования...")
    async with research_lock:
        status.started = time.monotonic()
        ticker = asyncio.create_task(status.tick())
        try:
            report = await asyncio.to_thread(_run_blocking, query, mode, sources, status)
        except PreflightError as e:
            await status.render(f"❌ {html.escape(str(e))}")
            return
        except Exception as e:
            logging.exception("Research failed: %s", query)
            await status.render(f"❌ Ошибка: {html.escape(str(e)[:300])}")
            return
        finally:
            ticker.cancel()
        await status.render(
            f"✅ Готово за {_mmss(report.seconds)}: {report.words} слов, {len(report.sources)} источников"
        )
        await send_report(status.message, library.get(report.id))


async def answer_question(message: Message, report_id: int, question: str) -> None:
    entry = library.get(report_id)
    if not entry or not entry.md_path.exists():
        await message.reply(f"Отчёт #{report_id} не найден. Список: /history")
        return
    thinking = await message.reply(f"🤔 Ищу ответ в отчёте #{report_id}...")
    try:
        answer = await ask(entry.md_path, question)
    except Exception as e:
        logging.exception("Ask failed")
        await thinking.edit_text(f"❌ Ошибка: {html.escape(str(e)[:300])}")
        return
    await thinking.delete()
    for part in _chunks(f"💬 По отчёту #{report_id}:\n\n{answer}"):
        await message.reply(part, parse_mode=None)


@dp.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@dp.message(Command("history"))
async def cmd_history(message: Message) -> None:
    entries = library.recent(15)
    if not entries:
        await message.answer("Отчётов пока нет. Пришлите тему.")
        return
    lines = [
        f"<b>#{e.id}</b> · {e.created[:10]} · {MODE_BUTTONS.get(e.mode, e.mode)} · {e.words} слов\n{html.escape(e.query)}"
        for e in entries
    ]
    for part in _chunks("\n\n".join(lines)):
        await message.answer(part)


@dp.message(Command("view"))
async def cmd_view(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Укажите номер: /view 3")
        return
    entry = library.get(int(command.args))
    if not entry:
        await message.answer(f"Отчёт #{command.args.strip()} не найден. Список: /history")
        return
    await send_report(message, entry)


@dp.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Укажите, что искать: /search натрий-ионные")
        return
    results = library.search(command.args)
    if not results:
        await message.answer("Ничего не нашлось.")
        return
    lines = [f"<b>#{e.id}</b> {html.escape(e.query)}\n<i>{html.escape(snippet)}</i>" for e, snippet in results]
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
    entry = library.get(int(command.args))
    if not entry:
        await message.answer(f"Отчёт #{command.args.strip()} не найден.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{entry.id}"),
        InlineKeyboardButton(text="Отмена", callback_data="cancel"),
    ]])
    await message.answer(f"Удалить отчёт #{entry.id} «{html.escape(entry.query)}» вместе с файлами?", reply_markup=keyboard)


@dp.message(Command("block"))
async def cmd_block(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /block example.com")
        return
    domain = sources.block(command.args.strip())
    await message.answer(f"🚫 {html.escape(domain)} больше не будет источником. Вернуть: /unblock {html.escape(domain)}")


@dp.message(Command("unblock"))
async def cmd_unblock(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /unblock example.com")
        return
    found = sources.unblock(command.args.strip())
    await message.answer("✅ Разблокирован." if found else "Этого домена нет в списке. Список: /blocked")


@dp.message(Command("blocked"))
async def cmd_blocked(message: Message) -> None:
    domains = sources.blocked_domains()
    await message.answer(
        "Заблокированные источники:\n" + "\n".join(html.escape(d) for d in domains) if domains
        else "Свой список пуст. Соцсети, видео и магазины отсеиваются всегда, остальное оценивает модель."
    )


@dp.callback_query(F.data.startswith("del:"))
async def on_delete(callback: CallbackQuery) -> None:
    entry = library.delete(int(callback.data.split(":")[1]))
    await callback.answer()
    await callback.message.edit_text(f"🗑 Отчёт #{entry.id} удалён." if entry else "Уже удалён.")


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
    key = f"{message.chat.id}_{message.message_id}"
    pending[key] = {"query": query, "sources": DEFAULT_SOURCES}
    await message.reply(
        f"🔎 <b>{html.escape(query)}</b>\n\nВыберите режим. Второй ряд — источники.",
        reply_markup=_topic_keyboard(key, DEFAULT_SOURCES),
    )


@dp.callback_query(F.data.startswith("s:"))
async def on_sources(callback: CallbackQuery) -> None:
    _, key, sources = callback.data.split(":")
    if key not in pending:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    pending[key]["sources"] = sources
    await callback.answer()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=_topic_keyboard(key, sources))


@dp.callback_query(F.data.startswith("m:"))
async def on_mode(callback: CallbackQuery) -> None:
    _, key, mode = callback.data.split(":")
    item = pending.pop(key, None)
    if not item:
        await callback.answer("Тема устарела, пришлите её ещё раз", show_alert=True)
        return
    await callback.answer()
    header = f"🔎 <b>{html.escape(item['query'])}</b>\n{MODE_BUTTONS[mode]} · {SOURCE_BUTTONS[item['sources']]}"
    status = Status(callback.message, header)
    await status.render("⏳ Начинаю...")
    task = asyncio.create_task(run_research(status, item["query"], mode, item["sources"]))
    background.add(task)
    task.add_done_callback(background.discard)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # TELEGRAM_TOKEN is the name DEEper v1 used, still set on the Railway service.
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("Нет TELEGRAM_BOT_TOKEN (в .env или в переменных окружения). Создайте бота у @BotFather и впишите токен.")
    allowed = {int(x) for x in re.findall(r"\d+", os.environ.get("TELEGRAM_ALLOWED_USERS", ""))}
    if not allowed:
        logging.warning("TELEGRAM_ALLOWED_USERS пуст: бот только сообщит каждому его Telegram ID")
    dp.message.outer_middleware(AllowedUsers(allowed))
    dp.callback_query.outer_middleware(AllowedUsers(allowed))
    library.sync()

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="history", description="Последние отчёты"),
        BotCommand(command="search", description="Поиск по отчётам"),
        BotCommand(command="view", description="Прислать отчёт N"),
        BotCommand(command="ask", description="Вопрос по отчёту N"),
        BotCommand(command="delete", description="Удалить отчёт N"),
        BotCommand(command="block", description="Не брать сайт в источники"),
        BotCommand(command="blocked", description="Заблокированные сайты"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    me = await bot.get_me()
    logging.info("Бот @%s запущен, доступ: %s", me.username, sorted(allowed) or "никому")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
