"""
Handler for /research command with mode selection via inline keyboard.
"""
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import RESEARCH_MODES
from bot.services.research_agent import ResearchAgent
from bot.utils.logger import get_logger

logger = get_logger("research_handler")
router = Router()

MAX_MSG = 4000

# Хранилище topic пока пользователь выбирает режим
_pending: dict = {}


def _split_message(text: str, limit: int = MAX_MSG):
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts


def _mode_keyboard(topic_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for mode_name, mode in RESEARCH_MODES.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{mode.label} — {mode.description}",
                callback_data=f"research:{mode_name}:{topic_key}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("research"))
async def research_handler(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply(
            "❌ Укажи тему исследования.\n\n"
            "Использование: /research &lt;тема&gt;\n"
            "Пример: /research квантовые вычисления"
        )
        return

    topic = args[1].strip()
    user_id = message.from_user.id

    # Сохраняем topic, ключ = user_id
    _pending[user_id] = topic

    await message.reply(
        f"🔬 <b>Тема:</b> {html.escape(topic)}\n\n"
        "Выбери режим исследования:",
        reply_markup=_mode_keyboard(str(user_id)),
    )


@router.callback_query(F.data.startswith("research:"))
async def mode_selected(callback: CallbackQuery, research_agent: ResearchAgent) -> None:
    await callback.answer()

    _, mode_name, user_key = callback.data.split(":", 2)
    user_id = callback.from_user.id
    username = callback.from_user.username or str(user_id)

    topic = _pending.pop(int(user_key), None)
    if not topic:
        await callback.message.edit_text("❌ Сессия истекла. Отправь /research снова.")
        return

    mode = RESEARCH_MODES.get(mode_name, RESEARCH_MODES["deep"])
    logger.info("Исследование от @{}: '{}' | режим: {}", username, topic[:80], mode.name)

    status_msg = await callback.message.edit_text(
        f"🚀 <b>Запускаю исследование</b>\n"
        f"📋 Тема: {html.escape(topic)}\n"
        f"⚙️ Режим: {mode.label}\n\n"
        "<i>Подожди, начинаю работу...</i>",
    )

    async def update_progress(text: str) -> None:
        try:
            await status_msg.edit_text(
                f"🔬 <b>{html.escape(topic)}</b>\n"
                f"⚙️ {mode.label}\n\n"
                f"{html.escape(text)}",
            )
        except Exception:
            pass

    try:
        result = await research_agent.research(
            topic=topic,
            mode_name=mode_name,
            progress_callback=update_progress,
        )

        research_id = result["research_id"]
        title = result["title"]
        report = result["report"]

        await status_msg.edit_text(
            f"✅ <b>Исследование #{research_id} готово!</b>\n"
            f"📋 {html.escape(title)}\n"
            f"⚙️ {mode.label}\n\n"
            "<i>Отправляю отчёт...</i>",
        )

        parts = _split_message(report)
        for i, part in enumerate(parts):
            prefix = f"📊 <b>Отчёт #{research_id}</b>\n\n" if i == 0 else ""
            await callback.message.answer(prefix + html.escape(part))

        logger.info("Отчёт #{} доставлен @{}", research_id, username)

    except Exception as e:
        logger.error("Ошибка исследования @{}: {}", username, e, exc_info=True)
        await status_msg.edit_text(
            f"❌ Ошибка: {html.escape(str(e)[:200])}\n\n"
            "Попробуй снова или измени тему."
        )
