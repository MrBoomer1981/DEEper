"""
Telegram Research Agent — main entry point.
Initialises all services, registers handlers, and starts polling.
"""
import asyncio
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

# ── Bootstrap logging before any other imports ────────────────────────────────
from bot.utils.logger import setup_logging, get_logger

setup_logging(logs_dir="logs")
logger = get_logger("main")

# ── Project imports ───────────────────────────────────────────────────────────
from bot.config import config
from bot.services.cache_manager import CacheManager
from bot.services.embeddings import EmbeddingService
from bot.services.knowledge_base import KnowledgeBase
from bot.services.pdf_processor import PDFProcessor
from bot.services.research_agent import ResearchAgent
from bot.services.memory import MemoryService
from bot.handlers import research_handler, pdf_handler, search_handler, history_handler, ask_handler, chat_handler


async def set_bot_commands(bot: Bot) -> None:
    """Register slash commands visible in Telegram UI."""
    commands = [
        BotCommand(command="research", description="Deep research on any topic"),
        BotCommand(command="pdf", description="Analyze a PDF document"),
        BotCommand(command="search", description="Search knowledge base"),
        BotCommand(command="history", description="Show saved research list"),
        BotCommand(command="view", description="View full research report"),
        BotCommand(command="delete", description="Delete a research entry"),
        BotCommand(command="help", description="Show help message"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    logger.info("Starting Telegram Research Agent...")

    # ── Ensure directories exist ──────────────────────────────────────────────
    os.makedirs("logs", exist_ok=True)
    os.makedirs("database", exist_ok=True)

    # ── Initialise services ───────────────────────────────────────────────────
    embedding_service = EmbeddingService(
        groq_api_key=config.groq_api_key,
        index_path=config.faiss_index_path,
    )
    knowledge_base = KnowledgeBase(
        db_path=config.db_path,
        embedding_service=embedding_service,
        max_researches=config.max_researches,
    )
    research_agent = ResearchAgent(
        config=config,
        knowledge_base=knowledge_base,
    )
    pdf_processor = PDFProcessor(
        groq_api_key=config.groq_api_key,
        primary_model=config.primary_model,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        max_pdf_mb=config.max_pdf_size_mb,
    )

    memory_service = MemoryService(db_path=config.db_path)

    # ── Aiogram Bot & Dispatcher ──────────────────────────────────────────────
    bot = Bot(
        token=config.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # ── Inject services via middleware-style workflow_data ─────────────────────
    dp["research_agent"] = research_agent
    dp["knowledge_base"] = knowledge_base
    dp["pdf_processor"] = pdf_processor
    dp["memory_service"] = memory_service

    # ── Register routers ──────────────────────────────────────────────────────
    dp.include_router(research_handler.router)
    dp.include_router(pdf_handler.router)
    dp.include_router(search_handler.router)
    dp.include_router(history_handler.router)
    dp.include_router(ask_handler.router)
    dp.include_router(chat_handler.router)  # Must be last — catches all text

    # ── Help handler (inline) ─────────────────────────────────────────────────
    from aiogram.filters import Command
    from aiogram.types import Message

    @dp.message(Command("start", "help"))
    async def help_handler(message: Message) -> None:
        await message.reply(
            "🤖 <b>DEEPer — Агент глубоких исследований</b>\n\n"
            "Провожу глубокий анализ любой темы по интернету и анализирую PDF.\n\n"
            "<b>Команды:</b>\n"
            "🔬 /research &lt;тема&gt; — Запустить исследование (выбор режима)\n"
            "💬 /ask &lt;id&gt; &lt;вопрос&gt; — Задать вопрос по исследованию\n"
            "🧠 /memory — Статус памяти разговора\n"
            "🗑 /forget — Очистить память\n"
            "📄 /pdf — Анализ PDF документа\n"
            "🔎 /search &lt;запрос&gt; — Поиск по базе знаний\n"
            "📚 /history — Список всех исследований\n"
            "👁 /view &lt;id&gt; — Полный отчёт\n"
            "🗑 /delete &lt;id&gt; — Delete a research entry\n"
            "❓ /help — Show this message\n\n"
            "<b>How it works:</b>\n"
            "1. I generate 15 search queries for your topic\n"
            "2. Search the web via Tavily\n"
            "3. Scrape up to 30 web pages\n"
            "4. Analyze content with AI\n"
            "5. Generate a structured report\n"
            "6. Save to your knowledge base\n\n"
            "<i>Research takes 2-5 minutes. Be patient! 🚀</i>"
        )

    # ── Set bot commands in Telegram UI ──────────────────────────────────────
    await set_bot_commands(bot)

    logger.info("Bot initialized. Starting polling...")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.critical("Fatal error: {}", e, exc_info=True)
        sys.exit(1)
