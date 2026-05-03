"""
Handler for /ask command — follow-up questions on saved research.
"""
import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.research_agent import ResearchAgent
from bot.utils.logger import get_logger

logger = get_logger("ask_handler")
router = Router()

MAX_MSG = 4000


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


@router.message(Command("ask"))
async def ask_handler(message: Message, research_agent: ResearchAgent) -> None:
    """
    /ask <id> <вопрос>
    Answer a question based on a saved research report.
    """
    parts = message.text.split(maxsplit=2)

    if len(parts) < 3 or not parts[1].strip().isdigit():
        await message.reply(
            "❌ Укажи ID исследования и вопрос.\n\n"
            "Использование: /ask &lt;id&gt; &lt;вопрос&gt;\n"
            "Пример: /ask 3 Какие главные риски?\n\n"
            "Используй /history чтобы увидеть все ID."
        )
        return

    research_id = int(parts[1])
    question = parts[2].strip()
    username = message.from_user.username or str(message.from_user.id)

    logger.info("Ask от @{} к исследованию #{}: '{}'", username, research_id, question[:60])

    status = await message.reply(
        f"🤔 Анализирую исследование <b>#{research_id}</b>...\n"
        f"<i>Вопрос: {html.escape(question)}</i>",
    )

    answer = await research_agent.ask(research_id, question)

    await status.edit_text(
        f"💬 <b>Вопрос по исследованию #{research_id}:</b>\n"
        f"<i>{html.escape(question)}</i>\n\n"
        f"{html.escape(answer)}"
    )
