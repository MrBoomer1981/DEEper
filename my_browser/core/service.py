"""Core's operations, shared by its web pages (direct calls) and its API (the bot and the worker)."""

import base64
import json

from my_browser.common.clients import call
from my_browser.common.settings import FACTCHECK_MODES
from my_browser.core import blocklist, qa, queue
from my_browser.core.library import Entry, Library


def entry_dict(entry: Entry) -> dict:
    return {"id": entry.id, "query": entry.query, "mode": entry.mode, "created": entry.created, "words": entry.words,
            "duration_sec": entry.duration_sec, "sources_count": entry.sources_count, "name": entry.base.name}


def meta(entry: Entry) -> dict:
    return json.loads(entry.base.with_suffix(".json").read_text("utf-8"))


def submit(query: str, mode: str, sources: str, writer: str, parent_id: int | None = None,
           origin: str = "web", origin_ref: dict | None = None, factcheck: bool | None = None) -> dict:
    """factcheck None: as the mode has it (FACTCHECK_MODES)."""
    parent = Library().get(parent_id) if parent_id else None
    return queue.submit(query, mode, sources, writer, parent.base.name if parent else None, origin, origin_ref,
                        mode in FACTCHECK_MODES if factcheck is None else factcheck)


def claim(worker: str) -> dict | None:
    """The next queued job for the worker, with the blocklist its source selection needs."""
    job = queue.claim(worker)
    if job:
        job["blocked"] = blocklist.blocked_domains()
    return job


def store_result(job_id: str, files_b64: dict[str, str]) -> int:
    files = {suffix: base64.b64decode(data) for suffix, data in files_b64.items()}
    report_id = Library().store(files)
    queue.finish(job_id, report_id, (json.loads(files[".json"]).get("cost") or {}).get("rub"))
    return report_id


def report_view(entry: Entry) -> dict:
    """Everything a client shows for one report."""
    library, data = Library(), meta(entry)
    parent = library.by_name(data["parent"]) if data.get("parent") else None
    children = library.children(entry)
    related = library.related(entry, exclude={c.id for c in children} | ({parent.id} if parent else set()))
    return {"entry": entry_dict(entry), "meta": data, "markdown": entry.md_path.read_text("utf-8"),
            "qa": qa.load_qa(entry.md_path), "parent": entry_dict(parent) if parent else None,
            "children": [entry_dict(c) for c in children], "related": [entry_dict(r) for r in related]}


async def ask(entry: Entry, question: str) -> dict:
    response = await call("worker", "POST", "/ask", json={"markdown": entry.md_path.read_text("utf-8"), "question": question})
    return qa.append(entry.md_path, question, response.json())


async def followups(entry: Entry) -> list[dict]:
    """Follow-ups for a report saved before they existed; kept in its JSON, the files stay as they are."""
    data = meta(entry)
    response = await call("worker", "POST", "/followups", json={"query": data["query"], "markdown": entry.md_path.read_text("utf-8")})
    result = response.json()
    if result["topics"]:
        data["followups"], data["followups_cost_rub"] = result["topics"], result["cost_rub"]
        entry.base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return result["topics"]


async def clarify(query: str) -> dict:
    """{questions: [{q, options}], cost_rub}"""
    return (await call("worker", "POST", "/clarify", json={"query": query})).json()


async def refine(query: str, answers: list[tuple[str, str]]) -> dict:
    """{topic, cost_rub}"""
    return (await call("worker", "POST", "/refine", json={"query": query, "answers": answers})).json()
