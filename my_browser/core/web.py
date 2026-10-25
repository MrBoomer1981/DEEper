"""Core's web app: DEEper's screens (my_browser/core/ui/, mobile first per DESIGN_HANDOFF.md) plus core's API.

    uv run app.py                    -> everything in one process at http://127.0.0.1:8501
    uv run python -m my_browser.core -> core alone (web app + API); the worker and bot run elsewhere

On Railway (PORT is set) it listens publicly and requires APP_PASSWORD; /api/* takes the service
token instead. Install: Safari -> «На экран Домой» on an iPhone, File -> Add to Dock on a Mac.
This module sets the app up (head, auth, static files, downloads, the library sync); each screen is a
module of core/ui/, imported below.
"""

import hashlib
import logging
import mimetypes
import os
import secrets

from my_browser.common.settings import PORT, REPORTS_DIR, ROOT, configure_logging

REPORTS_DIR.mkdir(exist_ok=True)
# Read by NiceGUI at import: keep login sessions on the Railway volume across redeploys.
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(REPORTS_DIR / ".nicegui"))

from fastapi import Request, Response  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from nicegui import app, ui  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from my_browser.core.api import router  # noqa: E402
from my_browser.core.library import Library  # noqa: E402
from my_browser.core.ui import kit  # noqa: E402
# tabs registers the tab bar's page (/, /radar, /library, /focus); the other screens register their own
from my_browser.core.ui import job, login, report, tabs  # noqa: E402,F401

OPEN_PATHS = ("/login", "/static/", "/_nicegui", "/favicon", "/api/")  # /api checks the service token itself


def asset_version() -> str:
    """Changes with app.css, app.js and the icon font, so a deploy is not hidden behind the browser cache."""
    digest = hashlib.sha1()
    for name in ("app.css", "app.js", "fonts/icons.woff2"):
        digest.update((ROOT / "static" / name).read_bytes())
    return digest.hexdigest()[:10]


VERSION = asset_version()
# NiceGUI's Material Icons cut to the icons DEEper shows (dev/icons.py): 5 KB instead of 129 KB on a first visit.
# Icons are ligatures, invisible until the font loads, and a browser fetches a font only once some text needs it:
# without the preload every new screen showed its icons a moment late. app.css sends .material-icons here.
ICON_FONT = f"/static/fonts/icons.woff2?v={VERSION}"
ui.add_head_html(f"""
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="DEEper">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#14100E">
<link rel="preload" href="/static/fonts/Onest.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/Unbounded.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/JetBrainsMono.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{ICON_FONT}" as="font" type="font/woff2" crossorigin>
<style>@font-face {{ font-family: 'DEEper Icons'; src: url('{ICON_FONT}') format('woff2'); font-display: block; }}</style>
<script>addEventListener('pagereveal', e => {{ if (e.viewTransition) document.documentElement.classList.add('vt'); }});</script>
<link rel="stylesheet" href="/static/app.css?v={VERSION}">
<script defer src="/static/app.js?v={VERSION}"></script>
""", shared=True)
# app.css, app.js and the icon font change their address with every change (?v=); the other fonts, the mascot and
# the app icons hardly ever change.
mimetypes.add_type("font/woff2", ".woff2")  # the slim Docker image does not know it and served the fonts as octet-stream
app.add_static_files("/static", ROOT / "static", max_cache_age=7 * 24 * 3600)
app.include_router(router)


@app.add_middleware
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not kit.AUTH or request.url.path.startswith(OPEN_PATHS) or app.storage.user.get("ok"):
            return await call_next(request)
        return RedirectResponse(f"/login?redirect_to={request.url.path}")


def storage_secret() -> str:
    path = REPORTS_DIR / ".storage_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


@app.get("/files/{report_id}/{kind}")
async def report_file(report_id: int, kind: str):
    entry = Library().get(report_id)
    path = entry.base.with_suffix(f".{kind}") if entry and kind in ("pdf", "docx", "md") else None
    if not path or not path.exists():
        return Response(status_code=404)
    return FileResponse(path, filename=path.name)


async def on_startup() -> None:
    Library().sync()


app.on_startup(on_startup)


def main() -> None:
    configure_logging()
    host, port = ("0.0.0.0" if kit.PUBLIC else "127.0.0.1"), PORT
    logging.info("Веб-интерфейс: http://%s:%s, пароль %s", host, port,
                 "задан" if kit.PASSWORD else ("НЕ задан" if kit.PUBLIC else "не нужен"))
    ui.run(
        host=host,
        port=port,
        title="DEEper",
        favicon=ROOT / "static" / "favicon.png",
        dark=True,
        language="ru",
        storage_secret=storage_secret(),
        viewport="width=device-width, initial-scale=1, viewport-fit=cover",  # safe areas of an installed iPhone app
        tailwind=False,  # every screen is styled by static/app.css; Tailwind would compile CSS in the phone's browser
        reconnect_timeout=60,  # a phone switching apps for a minute comes back to the same page, scroll position and all
        reload=False,
        show=False,
        show_welcome_message=not kit.PUBLIC,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
