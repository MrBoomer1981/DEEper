"""Run the bot as its own process: uv run python -m my_browser.bot (core must be reachable at CORE_URL)."""

import asyncio
import sys

from my_browser.common.settings import configure_logging
from my_browser.bot import telegram

if __name__ == "__main__":
    configure_logging()
    if not telegram.token():
        sys.exit("Нет TELEGRAM_BOT_TOKEN (в .env или в переменных окружения). Создайте бота у @BotFather и впишите токен.")
    asyncio.run(telegram.start())
