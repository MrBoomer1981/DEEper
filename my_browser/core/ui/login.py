"""Sign-in, when the app listens publicly (Railway) or APP_PASSWORD is set: one shared password, rate-limited per IP."""

import secrets
import time

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from my_browser.core.ui import kit

failed_logins: dict[str, list[float]] = {}


@ui.page("/login")
def login_page(redirect_to: str = "/") -> RedirectResponse | None:
    if not kit.AUTH or app.storage.user.get("ok"):
        return RedirectResponse("/")
    kit.shell("Вход")

    def try_login() -> None:
        ip = ui.context.client.request.client.host if ui.context.client.request.client else "?"
        recent = [t for t in failed_logins.get(ip, []) if time.time() - t < 300]
        if len(recent) >= 5:
            kit.toast("Слишком много попыток. Подождите 5 минут", "warn")
            return
        if kit.PASSWORD and secrets.compare_digest(password.value.encode(), kit.PASSWORD.encode()):
            app.storage.user["ok"] = True
            ui.navigate.to(redirect_to if redirect_to.startswith("/") else "/")
        else:
            failed_logins[ip] = recent + [time.time()]
            kit.toast("Неверный пароль", "error")

    with kit.screen("no-nav", "login"):
        with ui.element("div").classes("logo-row"):
            kit.graffiti("crown", "logo-crown")
            ui.html('DEE<span class="amber">per</span>', sanitize=False).classes("logo")
        kit.mascot("hello", 110)
        if not kit.PASSWORD:
            ui.label("На сервере не задан APP_PASSWORD — задайте его в переменных окружения.").classes("sub-text")
        password = ui.input(placeholder="Пароль", password=True, password_toggle_button=True) \
            .props("borderless dark autofocus").classes("field").on("keydown.enter", try_login)
        kit.primary("Войти", try_login)
    return None
