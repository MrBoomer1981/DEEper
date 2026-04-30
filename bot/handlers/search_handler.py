"""
Handler for /search command — semantic search over knowledge base.
"""
import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.knowledge_base import KnowledgeBase
from bot.utils.logger import get_logger

logger = get_logger("search_handler")
router = Router()


@router.message(Command("search"))
async def search_handler(message: Message, knowledge_base: KnowledgeBase) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply(
            "❌ Please provide a search query.\n\n"
            "Usage: /search &lt;query&gt;\n"
            "Example: /search quantum computing"
        )
        return

    query = args[1].strip()
    username = message.from_user.username or str(message.from_user.id)
    logger.info("Knowledge search from @{}: '{}'", username, query[:60])

    status = await message.reply(
        f"🔎 Searching knowledge base for:\n<b>{html.escape(query)}</b>",
    )

    try:
        results = await knowledge_base.semantic_search(query, top_k=5)

        if not results:
            await status.edit_text(
                "🔍 No matching research found in the knowledge base.\n\n"
                "Try running /research to add new research.",
            )
            return

        lines = [f"🔎 <b>Search results for:</b> <i>{html.escape(query)}</i>\n"]
        for rank, (research, distance) in enumerate(results, 1):
            relevance = max(0, round((1 - distance) * 100))
            lines.append(
                f"<b>{rank}. {html.escape(research.title)}</b> (ID: #{research.id})\n"
                f"📅 {research.formatted_timestamp()} | Relevance: {relevance}%\n"
                f"<i>{html.escape(research.summary[:200].strip())}...</i>\n"
            )

        await status.edit_text("\n".join(lines))
        logger.info("Search returned {} results to @{}", len(results), username)

    except Exception as e:
        logger.error("Search failed: {}", e, exc_info=True)
        await status.edit_text("❌ Search failed. Please try again.")
