"""
Handlers for /history, /view and /delete commands.
"""
import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.knowledge_base import KnowledgeBase
from bot.utils.logger import get_logger

logger = get_logger("history_handler")
router = Router()

MAX_TELEGRAM_MSG = 4000


def _split_message(text: str, limit: int = MAX_TELEGRAM_MSG):
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


@router.message(Command("history"))
async def history_handler(message: Message, knowledge_base: KnowledgeBase) -> None:
    username = message.from_user.username or str(message.from_user.id)
    logger.info("History requested by @{}", username)

    researches = knowledge_base.list_researches()

    if not researches:
        await message.reply(
            "📚 Knowledge base is empty.\n\n"
            "Use /research &lt;topic&gt; to start your first research!"
        )
        return

    lines = [f"📚 <b>Research History</b> ({len(researches)} entries)\n"]
    for r in researches:
        lines.append(
            f"<b>#{r.id}</b> — {html.escape(r.title)}\n"
            f"📅 {r.formatted_timestamp()} | 🔗 {len(r.sources)} sources\n"
            f"<i>{html.escape(r.summary[:120].strip())}...</i>\n"
        )

    full_text = "\n".join(lines)
    full_text += "\n\n<i>Use /delete &lt;id&gt; to remove • /view &lt;id&gt; to read full report</i>"

    for part in _split_message(full_text):
        await message.reply(part)


@router.message(Command("delete"))
async def delete_handler(message: Message, knowledge_base: KnowledgeBase) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.reply(
            "❌ Please provide a valid research ID.\n\n"
            "Usage: /delete &lt;id&gt;\n"
            "Example: /delete 3\n\n"
            "Use /history to see all research IDs."
        )
        return

    research_id = int(args[1].strip())
    username = message.from_user.username or str(message.from_user.id)

    research = knowledge_base.get_research(research_id)
    if not research:
        await message.reply(
            f"❌ Research #{research_id} not found.\n"
            "Use /history to see available IDs."
        )
        return

    success = await knowledge_base.delete_research(research_id)
    if success:
        await message.reply(
            f"🗑 Research <b>#{research_id}</b> deleted:\n"
            f"<i>{html.escape(research.title)}</i>"
        )
    else:
        await message.reply("❌ Failed to delete research. Please try again.")


@router.message(Command("view"))
async def view_handler(message: Message, knowledge_base: KnowledgeBase) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.reply(
            "❌ Please provide a valid research ID.\n\n"
            "Usage: /view &lt;id&gt;\n"
            "Example: /view 3"
        )
        return

    research_id = int(args[1].strip())
    research = knowledge_base.get_research(research_id)

    if not research:
        await message.reply(
            f"❌ Research #{research_id} not found.\n"
            "Use /history to see available IDs."
        )
        return

    parts = _split_message(research.report)
    for i, part in enumerate(parts):
        prefix = (
            f"📊 <b>Research #{research.id}</b> | {research.formatted_timestamp()}\n\n"
            if i == 0 else ""
        )
        await message.reply(prefix + html.escape(part))
