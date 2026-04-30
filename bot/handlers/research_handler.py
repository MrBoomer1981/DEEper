"""
Handler for /research command — runs the full deep research pipeline
and streams progress updates to the user.
"""
import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.research_agent import ResearchAgent
from bot.utils.logger import get_logger

logger = get_logger("research_handler")
router = Router()

MAX_TELEGRAM_MSG = 4000  # Telegram message character limit


def _split_message(text: str, limit: int = MAX_TELEGRAM_MSG):
    """Split long text into chunks that fit Telegram's message limit."""
    parts = []
    while len(text) > limit:
        # Try to split at a newline near the limit
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts


@router.message(Command("research"))
async def research_handler(message: Message, research_agent: ResearchAgent) -> None:
    """Handle /research <query> command."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply(
            "❌ Please provide a research topic.\n\n"
            "Usage: /research &lt;your topic&gt;\n"
            "Example: /research quantum computing applications"
        )
        return

    topic = args[1].strip()
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)

    logger.info("Research request from @{}: '{}'", username, topic[:80])

    safe_topic = html.escape(topic)

    # Send initial status message
    status_msg = await message.reply(
        f"🚀 Starting deep research on:\n<b>{safe_topic}</b>\n\n"
        "<i>This will take 2-5 minutes. I'll keep you updated...</i>",
    )

    # Progress callback — edits the status message
    async def update_progress(text: str) -> None:
        try:
            await status_msg.edit_text(
                f"🔬 <b>Researching:</b> {safe_topic}\n\n{html.escape(text)}",
            )
        except Exception:
            pass  # Ignore edit errors (e.g. message not modified)

    try:
        result = await research_agent.research(
            topic=topic,
            progress_callback=update_progress,
        )

        report = result["report"]
        title = result["title"]
        research_id = result["research_id"]

        # Final status update
        await status_msg.edit_text(
            f"✅ Research <b>#{research_id}</b> complete!\n"
            f"📋 <b>{html.escape(title)}</b>\n\n"
            f"Sending report...",
        )

        # Send report as plain text to avoid markdown parsing issues
        parts = _split_message(report)
        for i, part in enumerate(parts):
            if i == 0:
                await message.reply(
                    f"📊 <b>Research Report</b> (ID: {research_id})\n\n"
                    + html.escape(part),
                )
            else:
                await message.reply(html.escape(part))

        logger.info("Research #{} delivered to @{}", research_id, username)

    except Exception as e:
        logger.error("Research failed for @{}: {}", username, e, exc_info=True)
        await status_msg.edit_text(
            f"❌ Research failed: {html.escape(str(e)[:200])}\n\n"
            "Please try again or rephrase your topic.",
        )
