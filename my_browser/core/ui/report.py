"""«Отчёт» (/report/{id}): the text to read, nothing else on the main screen.

The top bar has «Назад», the section counter «3 / 9 ▾» (follows the scroll in app.js; a tap opens the
contents) and «⋯» (files, sources, questions, the end blocks, delete). Citations in parentheses become
amber superscript numbers, and the sources sheet is numbered the same way; links inside a sentence stay
text. Sentences the fact-check could not confirm get a bordo «?». Selecting text shows a toolbar (app.js):
«Сохранить» (a bookmark), «Спросить» (the quote into the question pill), «Глубже» (a new research on the
quote that continues this report). Answers to questions open as a sheet; the history is in «⋯».
"""

import html
import json
import re

from nicegui import ui

from my_browser.common.report import FACTCHECK_TITLE, KINDS, VERDICT_TEXT, appended, body
from my_browser.common.text import MARKDOWN_LINK, plural, rub_text
from my_browser.core import blocklist, bookmarks, service
from my_browser.core.library import Library
from my_browser.core.qa import load_qa
from my_browser.core.ui import kit

# a parenthesized group of citations only: «([IEA](…))», «([A](…); [B](…))»
CITATIONS = re.compile(r"[ \t]*\(\s*(?:\[[^\]]*\]\(https?://(?:[^()\s]|\([^()\s]*\))+\)\s*[;,]?\s*)+\)")  # with the space before it
H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$", re.M)


def mark_doubtful(markdown: str, doubtful: list[dict]) -> str:
    """A bordo «?» after each sentence the fact-check could not confirm; it shows the verdict and leads to the list."""
    for item in doubtful:
        if not item.get("raw"):
            continue
        verdict = VERDICT_TEXT.get(item["verdict"], item["verdict"])
        tip = html.escape(f"{verdict}: {item['note']}" if item["note"] else verdict, quote=True)
        mark = f' <sup class="fc-mark" title="{tip}"><a href="#factcheck">?</a></sup>'
        pattern = r"\s+".join(map(re.escape, item["raw"].split()))
        markdown = re.sub(f"({pattern})(?! <sup class=\"fc-mark\")", lambda m: m.group(1) + mark, markdown, count=1)
    return markdown


def cite(markdown: str) -> tuple[str, dict[str, int]]:
    """Citation groups as superscript numbers, numbered by first appearance; returns {url: number} too."""
    numbers: dict[str, int] = {}

    def group(match: re.Match) -> str:
        links = []
        for link in MARKDOWN_LINK.finditer(match.group(0)):
            anchor, url = link.group(1), link.group(2)
            n = numbers.setdefault(url, len(numbers) + 1)
            links.append(f'<a href="{html.escape(url, quote=True)}" title="{html.escape(anchor, quote=True)}">{n}</a>')
        return f'<sup class="cite">{",".join(links)}</sup>'

    return CITATIONS.sub(group, markdown), numbers


@ui.page("/report/{report_id}")
def report_page(report_id: int) -> None:
    kit.shell("Отчёт")
    library = Library()
    entry = library.get(report_id)
    if not entry or not entry.md_path.exists():
        with kit.screen("no-nav"):
            with ui.element("div").classes("topbar"):
                kit.back("/library")
            with ui.element("div").classes("empty"):
                kit.mascot("hello", 110)
                ui.label("Отчёт не найден.").classes("sub-text")
        return
    markdown = entry.md_path.read_text("utf-8")
    meta = json.loads(entry.base.with_suffix(".json").read_text("utf-8"))
    check = meta.get("factcheck") or {}
    text, numbers = cite(mark_doubtful(body(markdown), check.get("doubtful") or []))
    headings = [m.group(1).strip() for m in H2.finditer(body(markdown))]
    origin = library.by_name(meta["parent"]) if meta.get("parent") else None

    def contents() -> None:
        dialog, sheet_body = kit.sheet("Содержание")
        with sheet_body, ui.element("div").classes("list"):
            for i, title in enumerate(headings):
                kit.row(re.sub(r"[*_`]", "", title), on_click=lambda i=i: (dialog.close(), ui.run_javascript(f"deeperScrollTo({i})")))
        dialog.open()

    def sources() -> None:
        dialog, sheet_body = kit.sheet(f"Источники · {len(meta['sources'])}")
        blocked = set(blocklist.blocked_domains())

        def block(d: str, button) -> None:
            blocklist.block(d)
            button.classes("done")
            kit.toast(f"{d} больше не будет источником")

        cited = sorted(numbers, key=numbers.get)
        others = [u for u in meta["sources"] if u not in numbers]
        with sheet_body, ui.element("div").classes("list"):
            for url in cited + others:
                d = kit.domain(url)
                with ui.element("div").classes("li compact source"):
                    ui.label(str(numbers[url]) if url in numbers else "·").classes("cite-no")
                    with ui.link(target=url, new_tab=True).classes("li-text plain"):
                        ui.label(d).classes("li-title lines-1")
                        ui.label(url).classes("li-sub url")
                    button = kit.icon_button("block", f"Не использовать {d}")
                    button.on("click", lambda d=d, b=button: block(d, b))
                    if d in blocked:
                        button.classes("done")
        dialog.open()

    def show_answer(sheet_body, item: dict) -> None:
        with sheet_body:
            ui.label(item["q"]).classes("question")
            kit.markdown(item["a"]).classes("report compact")
            if item.get("research"):
                kit.row(item["research"], "исследовать отдельно", target=kit.follow_up_url(item["research"], "quick", entry.id), accent=True)
            if item.get("cost_rub") is not None:
                ui.label(rub_text(item["cost_rub"])).classes("mono-label")

    def questions() -> None:
        dialog, sheet_body = kit.sheet("Вопросы")
        history = load_qa(entry.md_path)
        if not history:
            with sheet_body:
                ui.label("Вопросов пока не было. Спросите внизу экрана — ответ строится только по тексту отчёта.").classes("sub-text")
        for item in history:
            show_answer(sheet_body, item)
        dialog.open()

    async def ask() -> None:
        q = (field.value or "").strip()
        if not q:
            return
        dialog, sheet_body = kit.sheet("Вопрос по отчёту")
        with sheet_body:
            ui.label(q).classes("question")
            waiting = ui.label("Ищу ответ в отчёте…").classes("mono-label")
        dialog.open()
        field.value = ""
        try:
            item = await service.ask(entry, q)
        except Exception as e:
            waiting.set_text(f"Не получилось: {e}")
            return
        sheet_body.clear()
        show_answer(sheet_body, item)

    def delete() -> None:
        dialog, sheet_body = kit.sheet("Удалить отчёт?")
        with sheet_body:
            ui.label(f"«{entry.query}» и его файлы удалятся без возврата.").classes("sub-text")
            ui.button("Удалить", on_click=lambda: (library.delete(entry.id), ui.navigate.to("/library"))) \
                .props("unelevated no-caps color=bordo text-color=paper").classes("btn-primary")
            kit.secondary("Отмена", dialog.close)
        dialog.open()

    def more() -> None:
        dialog, sheet_body = kit.sheet()
        facts = [kit.day(entry.created), kit.MODE_NAMES.get(entry.mode, entry.mode),
                 f"{kit.number(entry.words)} {plural(entry.words, 'слово', 'слова', 'слов')}",
                 f"{entry.sources_count} {plural(entry.sources_count, 'источник', 'источника', 'источников')}", kit.mmss(entry.duration_sec)]
        if meta.get("writer"):
            facts.append(f"текст: {kit.WRITER_OF_MODEL.get(meta['writer'], meta['writer'])}")
        if meta.get("cost"):
            facts.append(rub_text(meta["cost"].get("rub")))
        if check.get("checked"):
            facts.append(f"факты: {check['checked'] - len(check.get('doubtful') or [])} из {check['checked']}")
        with sheet_body:
            ui.label(entry.query).classes("sheet-title long")
            ui.label(" · ".join(facts)).classes("mono-label")
            with ui.element("div").classes("list"):
                for kind, name in (("pdf", "PDF"), ("docx", "DOCX"), ("md", "Markdown")):
                    if entry.base.with_suffix(f".{kind}").exists():
                        kit.row(f"Скачать {name}", on_click=lambda k=kind: ui.download.from_url(f"/files/{entry.id}/{k}"), chevron=True)
                kit.row("Источники", value=str(len(meta["sources"])), chevron=True, on_click=lambda: (dialog.close(), sources()))
                kit.row("Вопросы", value=str(len(load_qa(entry.md_path))), chevron=True, on_click=lambda: (dialog.close(), questions()))
                if check.get("checked"):
                    kit.row("Проверка фактов", chevron=True, on_click=lambda: (dialog.close(), ui.run_javascript("deeperScrollTo('#factcheck')")))
                kit.row("Что исследовать дальше", chevron=True, on_click=lambda: (dialog.close(), ui.run_javascript("deeperScrollTo('#next')")))
                kit.row("Удалить отчёт", on_click=lambda: (dialog.close(), delete()))
        dialog.open()

    async def propose(button) -> None:
        button.props("loading")
        try:
            found = await service.followups(entry)
        except Exception as e:
            kit.toast(f"Не получилось подобрать темы: {e}", "error")
            return
        finally:
            button.props(remove="loading")
        if not found:
            kit.toast("Не получилось подобрать темы, попробуйте ещё раз", "warn")
            return
        meta.update(json.loads(entry.base.with_suffix(".json").read_text("utf-8")))
        next_steps.refresh()

    def selected(e) -> None:
        action, quote = e.args.get("action"), " ".join(str(e.args.get("text") or "").split())
        if not quote:
            return
        if action == "save":
            bookmarks.add(entry.id, entry.base.name, quote, str(e.args.get("section") or ""))
            kit.toast("Сохранено в Библиотеку → Закладки", "ok")
        elif action == "ask":
            field.value = f"«{quote[:300]}» — "
            ui.run_javascript("document.querySelector('.ask-pill textarea')?.focus()")
        elif action == "dig":
            ui.navigate.to(kit.follow_up_url(quote[:300], "quick", entry.id))

    ui.on("deeper_selection", selected)

    with kit.screen("no-nav", "with-pill", "reading"):
        with ui.element("div").classes("topbar sticky"):
            kit.back("/library")
            if headings:
                with ui.element("button").classes("toc-button").props('type="button" aria-label="Содержание"') as toc:
                    ui.label(f"1 / {len(headings)} ▾").classes("toc-label")
                toc.on("click", contents)
            kit.icon_button("more_horiz", "Ещё: файлы, источники, вопросы", more)
        if origin:
            with ui.link(target=f"/report/{origin.id}").classes("continue linked"):
                ui.icon("subdirectory_arrow_right")
                ui.label(f"продолжение отчёта #{origin.id}: {origin.query}").classes("continue-topic")
        kit.markdown(text).classes("report main")

        if checks := appended(markdown, FACTCHECK_TITLE):
            with ui.element("section").classes("end-block"):
                ui.html('<a id="factcheck" class="anchor"></a>', sanitize=False)
                ui.label("Проверка фактов").classes("sheet-label")
                kit.markdown(checks).classes("report compact")

        @ui.refreshable
        def next_steps() -> None:
            topics = meta.get("followups") or []
            children = library.children(entry)
            related = library.related(entry, exclude={c.id for c in children} | ({origin.id} if origin else set()))
            with ui.element("section").classes("end-block"):
                ui.html('<a id="next" class="anchor"></a>', sanitize=False)
                with ui.element("div").classes("next-head"):
                    ui.label("Что исследовать дальше").classes("sheet-label")
                    with ui.element("div").classes("next-dip"):
                        kit.mascot("found", 72)
                        kit.marker("NEXT?", "next-marker")
                if topics:
                    with ui.element("div").classes("list"):
                        for t in topics:
                            kit.row(t["query"], f"{KINDS[t['kind']].lower()} · {kit.MODE_NAMES.get(t['mode'], t['mode'])}"
                                    + (f" · {t['why']}" if t["why"] else ""),
                                    target=kit.follow_up_url(t["query"], t["mode"], entry.id), lines=3)
                else:
                    propose_button = kit.secondary("Предложить темы", lambda: propose(propose_button), icon="auto_awesome")
                    ui.label("модель прочтёт оглавление и выводы · ≈\u00a00,1\u00a0₽").classes("mono-label")
                for title, items in (("Продолжения этого отчёта", children), ("Похожие в библиотеке", related)):
                    if items:
                        ui.label(title).classes("sheet-label")
                        with ui.element("div").classes("list"):
                            for e in items:
                                kit.row(e.query, f"#{e.id} · {kit.day(e.created)}", target=f"/report/{e.id}")

        next_steps()

    ui.element("div").classes("pill-scrim").props('aria-hidden="true"')
    with ui.element("div").classes("pill bottom ask-pill").props('role="form"'):
        field = ui.textarea(placeholder="Спросить по отчёту") \
            .props('borderless dense dark autogrow rows=1 enterkeyhint=send aria-label="Вопрос по отчёту"').classes("pill-input")
        field.on("keydown.enter.exact.prevent", ask)
        field.on("keydown.meta.enter", ask)
        field.on("keydown.ctrl.enter", ask)
        kit.icon_button("arrow_upward", "Спросить", ask).classes("send")
