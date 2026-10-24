"""HTTP calls between the parts.

Core is always reached over HTTP (it is the web server). Worker, search and news are reached at
WORKER_URL / SEARCH_URL / NEWS_URL when they run as their own processes, otherwise in-process through
their ASGI apps: the same requests and the same API, without a network hop. A client is opened per call
because each research runs in its own event loop.
"""

import functools
import importlib

import httpx

from my_browser.common.settings import CORE_URL, NEWS_URL, SEARCH_URL, WORKER_URL, service_token

_APPS = {"worker": "my_browser.worker.api", "search": "my_browser.search.api", "news": "my_browser.news.api"}


@functools.cache
def _app(part: str):
    return importlib.import_module(_APPS[part]).app


def _options(part: str, timeout: float) -> dict:
    options = {"headers": {"Authorization": f"Bearer {service_token()}"}, "timeout": timeout}
    url = {"core": CORE_URL, "worker": WORKER_URL, "search": SEARCH_URL, "news": NEWS_URL}[part]
    if url:
        return {**options, "base_url": url.rstrip("/")}
    return {**options, "base_url": f"http://{part}", "transport": httpx.ASGITransport(app=_app(part))}


async def call(part: str, method: str, path: str, timeout: float = 120, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(**_options(part, timeout)) as client:
        response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response


def call_core(method: str, path: str, timeout: float = 60, **kwargs) -> httpx.Response:
    """Blocking call to core, for the worker's job loop (a plain thread)."""
    with httpx.Client(**_options("core", timeout)) as client:
        response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response
