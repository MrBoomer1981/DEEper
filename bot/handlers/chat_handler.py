"""
General chat handler with conversation memory.
Handles free-form messages that aren't commands.
The bot remembers context across sessions per user.
"""
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.groq_rotator import GroqKeyRotator
from bot.services.memory import MemoryService
from bot.config import config
from bot.utils.logger import get_logger
from bot.utils.text_utils import truncate_text

logger = get_logger("chat_handler")
router = Router()

SYSTEM_PROMPT = """Ты — DEEPer, продвинутый AI-агент для глубоких исследований.

Твои возможности:
- /research <тема> — глубокое исследование (3 режима: простой, углублённый, изучение)
- /ask <id> <вопрос> — вопрос по сохранённому исследованию
- /search <запрос> — поиск по базе знаний
- /history — список исследований
- /view <id> — полный отчёт
- /pdf — анализ PDF

Ты помнишь контекст нашего разговора и используешь его в ответах.
Отвечай по-русски, дружелюбно и по существу.
Если пользователь хочет исследовать тему — предложи использовать /research."""


class ChatService:
    def __init__(self) -> None:
        self.rotator = GroqKeyRotator()

    async def reply(
        self,
        user_id: int,
        user_message: str,
        memory: MemoryService,
    ) -> str:
        # Get conversation history
        history = memory.get_context(user_id)

        # Build messages: system + history + new message
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        return await self.rotator.chat(
            model=config.primary_model,
            messages=messages,
            max_tokens=1000,
            temperature=0.7,
        )


_chat_service = ChatService()

MAX_MSG = 4000


@router.message(Command("memory"))
async def memory_info_handler(message: Message, memory_service: MemoryService) -> None:
    """Show memory status for current user."""
    user_id = message.from_user.id
    count = memory_service.count(user_id)
    context = memory_service.get_context(user_id)

    if not context:
        await message.reply(
            "🧠 <b>Память</b>\n\n"
            "История разговора пуста.\n"
            "Просто напиши мне что-нибудь — я запомню контекст."
        )
        return

    preview_lines = []
    for msg in context[-5:]:  # show last 5
        role_icon = "👤" if msg["role"] == "user" else "🤖"
        preview_lines.append(f"{role_icon} {html.escape(msg['content'][:80])}...")

    await message.reply(
        f"🧠 <b>Память разговора</b>\n\n"
        f"Сохранено сообщений: <b>{count}</b>\n"
        f"В контексте LLM: до 12 последних\n\n"
        f"<b>Последние сообщения:</b>\n"
        + "\n".join(preview_lines) + "\n\n"
        f"Используй /forget чтобы очистить память."
    )


@router.message(Command("forget"))
async def forget_handler(message: Message, memory_service: MemoryService) -> None:
    """Clear conversation memory for user."""
    user_id = message.from_user.id
    memory_service.clear(user_id)
    await message.reply(
        "🗑 <b>Память очищена</b>\n\n"
        "Я забыл весь контекст нашего разговора.\n"
        "Начинаем с чистого листа!"
    )


@router.message(F.text & ~F.text.startswith("/"))
async def free_chat_handler(message: Message, memory_service: MemoryService) -> None:
    """Handle free-form text messages with memory context."""
    user_id = message.from_user.id
    user_text = message.text.strip()
    username = message.from_user.username or str(user_id)

    if len(user_text) < 2:
        return

    logger.info("Chat от @{}: '{}'", username, user_text[:60])

    # Save user message to memory
    memory_service.add_message(user_id, "user", user_text)

    # Show typing indicator
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        reply_text = await _chat_service.reply(user_id, user_text, memory_service)

        # Save bot reply to memory
        memory_service.add_message(user_id, "assistant", reply_text)

        # Send response
        if len(reply_text) > MAX_MSG:
            parts = []
            while len(reply_text) > MAX_MSG:
                split_at = reply_text.rfind("\n", 0, MAX_MSG)
                if split_at == -1:
                    split_at = MAX_MSG
                parts.append(reply_text[:split_at])
                reply_text = reply_text[split_at:].lstrip()
            parts.append(reply_text)
            for part in parts:
                await message.reply(html.escape(part))
        else:
            await message.reply(html.escape(reply_text))

    except Exception as e:
        logger.error("Chat error для @{}: {}", username, e)
        await message.reply(
            "❌ Не удалось обработать сообщение. Попробуй ещё раз."
        )
