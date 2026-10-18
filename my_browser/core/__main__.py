"""Run core alone (web app + API): uv run python -m my_browser.core. The worker and the bot run as their own processes."""

from my_browser.core import web

if __name__ in {"__main__", "__mp_main__"}:
    web.main()
