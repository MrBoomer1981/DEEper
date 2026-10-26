"""Core's operations, shared by its web pages (direct calls) and its API (the bot and the worker).

A branch is a report and the reports it continues (`parent`, by file name). A job that continues one is
claimed with what the reader already read (`branch`: the nearest reports' key points) and the route the
branch follows, if its first report proposed one (`route`): the worker's writer and follow-ups use them.
"""

import asyncio
import base64
import json
import logging

from my_browser.common.clients import call
from my_browser.common.report import summary
from my_browser.common.settings import FACTCHECK_MODES
from my_browser.core import blocklist, jobs, qa, short
from my_browser.core.library import Entry, Library

logger = logging.getLogger(__name__)

BRANCH_REPORTS = 4  # earlier reports of a branch the next one is told about
LINEAGE_LIMIT = 12  # how far up a branch its route is looked for


def entry_dict(entry: Entry) -> dict:
    return {"id": entry.id, "query": entry.query, "mode": entry.mode, "created": entry.created, "words": entry.words,
            "duration_sec": entry.duration_sec, "sources_count": entry.sources_count, "name": entry.base.name,
            "reading_min": entry.reading_min}


def meta(entry: Entry) -> dict:
    return json.loads(entry.base.with_suffix(".json").read_text("utf-8"))


def submit(query: str, mode: str, sources: str, writer: str, parent_id: int | None = None,
           origin: str = "web", origin_ref: dict | None = None, factcheck: bool | None = None,
           route_step: int | None = None, hold: float = 0, profile: str = "") -> dict:
    """factcheck None: as the mode has it (FACTCHECK_MODES). route_step: the step of the parent's route this research
    is; hold: seconds before the worker may take it (one tap on a follow-up); profile: the reader's interests."""
    parent = Library().get(parent_id) if parent_id else None
    return jobs.submit(query, mode, sources, writer, parent.base.name if parent else None, origin, origin_ref,
                       mode in FACTCHECK_MODES if factcheck is None else factcheck,
                       route_step=route_step if parent else None, hold=hold, profile=profile)


def lineage(entry: Entry, library: Library | None = None, limit: int = LINEAGE_LIMIT) -> list[tuple[Entry, dict]]:
    """The report and the ones it continues, nearest first, each with its JSON; stops at a missing parent or a loop."""
    library = library or Library()
    chain, seen = [], set()
    while entry and entry.id not in seen and len(chain) < limit:
        seen.add(entry.id)
        try:
            data = meta(entry)
        except (OSError, ValueError):
            break
        chain.append((entry, data))
        entry = library.by_name(data["parent"]) if data.get("parent") else None
    return chain


def route(chain: list[tuple[Entry, dict]]) -> dict | None:
    """The route the branch of chain[0] follows, or None: {"root": the report that proposed it, "steps": [{query, why}]
    (that report's topic is step 1), "at": the step chain[0] is, or None when it went off the route, "reached": the
    furthest step on the way to it, "next": the step to offer next, or None when the route is done}."""
    for i, (entry, data) in enumerate(chain):
        if data.get("route"):
            steps = [{"query": entry.query, "why": ""}, *data["route"]]
            reached = max([1, *(d.get("route_step") or 0 for _, d in chain[:i])])
            at = 1 if i == 0 else chain[0][1].get("route_step")
            return {"root": entry, "steps": steps, "at": at, "reached": min(reached, len(steps)),
                    "next": reached + 1 if reached < len(steps) else None}
    return None


def branch(chain: list[tuple[Entry, dict]]) -> list[dict]:
    """What the reader read before the next report of this branch: the nearest reports' topics and key points."""
    found = []
    for entry, _ in chain[:BRANCH_REPORTS]:
        try:
            found.append({"id": entry.id, "query": entry.query, "summary": summary(entry.md_path.read_text("utf-8"))})
        except OSError:
            continue
    return found


async def own_profile() -> str:
    """The profile the user wrote with the bot's /profile (the news part keeps it, without contacts); "" without one."""
    try:
        data = (await call("news", "GET", "/profile", timeout=5)).json()
    except Exception as e:
        logger.warning("No profile from news: %s", e)
        return ""
    return data["text"] if data.get("custom") else ""


async def reader_profile(interests: str = "") -> str:
    """What the follow-ups «Для меня» know about the reader: interests from «Фокус» and the bot's /profile."""
    parts = [f"Интересы: {interests.strip()}." if interests.strip() else "", await own_profile()]
    return " ".join(part for part in parts if part)


async def claim(worker: str) -> dict | None:
    """The next queued job for the worker, with the blocklist its source selection needs, and for a job that
    continues a branch what the reader already read and the route the branch follows."""
    job = jobs.claim(worker)
    if not job:
        return None
    job["blocked"] = blocklist.blocked_domains()
    library = Library()
    parent = library.by_name(job["parent"]) if job.get("parent") else None
    chain = lineage(parent, library) if parent else []
    job["branch"] = branch(chain)
    found = route(chain) if chain else None
    job["route"] = {"steps": found["steps"], "at": job.get("route_step")} if found else None
    job["profile"] = await reader_profile(job.get("profile") or "")
    job.pop("draft", None)
    return job


def store_result(job_id: str, files_b64: dict[str, str]) -> int:
    files = {suffix: base64.b64decode(data) for suffix, data in files_b64.items()}
    report_id = Library().store(files)
    jobs.finish(job_id, report_id, (json.loads(files[".json"]).get("cost") or {}).get("rub"))
    return report_id


def route_dict(found: dict | None) -> dict | None:
    """A route as clients get it: the root report's number instead of its entry."""
    return {**found, "root": found["root"].id} if found else None


def report_view(entry: Entry) -> dict:
    """Everything a client shows for one report."""
    library, data = Library(), meta(entry)
    parent = library.by_name(data["parent"]) if data.get("parent") else None
    children = library.children(entry)
    related = library.related(entry, exclude={c.id for c in children} | ({parent.id} if parent else set()))
    return {"entry": entry_dict(entry), "meta": data, "markdown": entry.md_path.read_text("utf-8"),
            "qa": qa.load_qa(entry.md_path), "parent": entry_dict(parent) if parent else None,
            "children": [entry_dict(c) for c in children], "related": [entry_dict(r) for r in related],
            "route": route_dict(route(lineage(entry, library)))}


async def ask(entry: Entry, question: str) -> dict:
    response = await call("worker", "POST", "/ask", json={"markdown": entry.md_path.read_text("utf-8"), "question": question})
    return qa.append(entry.md_path, question, response.json())


async def followups(entry: Entry, interests: str = "") -> list[dict]:
    """Follow-ups for a report saved before they existed (and a route, if it starts a branch); kept in its JSON,
    the files stay as they are."""
    chain = lineage(entry)
    data, found = chain[0][1], route(chain)
    response = await call("worker", "POST", "/followups", json={
        "query": data["query"], "markdown": entry.md_path.read_text("utf-8"), "branch": branch(chain[1:]),
        "profile": await reader_profile(interests), "route": found["steps"] if found else [],
        "make_route": not data.get("parent") and not found})
    result = response.json()
    if result["topics"]:
        data["followups"], data["followups_cost_rub"] = result["topics"], result["cost_rub"]
        if result.get("route"):
            data["route"] = result["route"]
        entry.base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return result["topics"]


_shortening: dict[int, asyncio.Lock] = {}  # one worker call per report: a second tap waits for the first


async def shorten(entry: Entry) -> dict:
    """{markdown, words, cost_rub}: the report's short version, written by the worker once and then read from its file.
    cost_rub is None when it was already there."""
    async with _shortening.setdefault(entry.id, asyncio.Lock()):
        if (text := short.load_short(entry.md_path)) is not None:
            return {"markdown": text, "words": short.words(text), "cost_rub": None}
        # Pro condenses a 5 000-word report in one to two minutes, over the default timeout of a call
        response = await call("worker", "POST", "/shorten", json={"markdown": entry.md_path.read_text("utf-8")}, timeout=300)
        result = response.json()
        short.save_short(entry.md_path, result["markdown"])
        return result


async def clarify(query: str) -> dict:
    """{questions: [{q, options}], cost_rub}"""
    return (await call("worker", "POST", "/clarify", json={"query": query})).json()


async def refine(query: str, answers: list[tuple[str, str]]) -> dict:
    """{topic, cost_rub}"""
    return (await call("worker", "POST", "/refine", json={"query": query, "answers": answers})).json()
