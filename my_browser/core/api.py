"""Core's HTTP API for the bot and the worker; core's own web pages call core/service.py directly."""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from my_browser.common.auth import require_token
from my_browser.common.settings import DEFAULT_SOURCES, DEFAULT_WRITER
from my_browser.core import blocklist, queue, service
from my_browser.core.library import Entry, Library

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _entry(report_id: int) -> Entry:
    entry = Library().get(report_id)
    if not entry or not entry.md_path.exists():
        raise HTTPException(404, "report not found")
    return entry


def _job(job: dict | None) -> dict:
    if not job:
        raise HTTPException(404, "job not found")
    return job


class JobRequest(BaseModel):
    query: str
    mode: str = "detailed"
    sources: str = DEFAULT_SOURCES
    writer: str = DEFAULT_WRITER
    parent_id: int | None = None  # the report this research continues
    origin: str = "telegram"
    origin_ref: dict | None = None
    factcheck: bool | None = None  # None: as the mode has it


class WorkerRequest(BaseModel):
    worker: str


class ProgressRequest(WorkerRequest):
    phase: str = ""
    queries: list = []
    urls: list = []
    log: list = []
    cost_rub: float | None = None


class ResultRequest(WorkerRequest):
    files: dict[str, str]  # suffix -> base64


class ErrorRequest(WorkerRequest):
    error: str


class QuestionRequest(BaseModel):
    question: str


class ClarifyRequest(BaseModel):
    query: str


class RefineRequest(BaseModel):
    query: str
    answers: list[tuple[str, str]]


class DomainRequest(BaseModel):
    domain: str


# jobs: the bot submits and follows them
@router.post("/jobs")
def create_job(request: JobRequest) -> dict:
    return service.submit(**request.model_dump())


@router.get("/jobs")
def list_jobs(status: str = "active", origin: str = "telegram") -> list[dict]:
    """Active jobs, or interrupted ones from `origin` nobody has been told about yet."""
    return queue.interrupted(origin) if status == "interrupted" else queue.active()


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _job(queue.get(job_id))


@router.post("/jobs/{job_id}/restart")
def restart_job(job_id: str) -> dict:
    return _job(queue.restart(job_id))


@router.post("/jobs/{job_id}/notified")
def job_notified(job_id: str) -> dict:
    queue.mark_notified(job_id)
    return {"ok": True}


# the worker's side of the queue
@router.post("/worker/hello")
def worker_hello(request: WorkerRequest) -> dict:
    queue.hello(request.worker)
    return {"ok": True}


@router.post("/worker/claim")
def worker_claim(request: WorkerRequest):
    return service.claim(request.worker) or Response(status_code=204)


@router.post("/worker/jobs/{job_id}/progress")
def worker_progress(job_id: str, request: ProgressRequest) -> dict:
    queue.progress(job_id, request.worker, request.model_dump())
    return {"ok": True}


@router.post("/worker/jobs/{job_id}/result")
def worker_result(job_id: str, request: ResultRequest) -> dict:
    return {"report_id": service.store_result(job_id, request.files)}


@router.post("/worker/jobs/{job_id}/error")
def worker_error(job_id: str, request: ErrorRequest) -> dict:
    queue.fail(job_id, request.error)
    return {"ok": True}


# reports
@router.get("/reports")
def list_reports(q: str = "", limit: int = 15) -> list[dict]:
    library = Library()
    if q:
        return [{**service.entry_dict(e), "snippet": snippet} for e, snippet in library.search(q, limit)]
    return [service.entry_dict(e) for e in library.recent(limit)]


@router.get("/reports/{report_id}")
def get_report(report_id: int) -> dict:
    return service.report_view(_entry(report_id))


@router.get("/reports/{report_id}/files/{kind}")
def get_report_file(report_id: int, kind: str):
    path = _entry(report_id).base.with_suffix(f".{kind}")
    if kind not in ("pdf", "docx", "md") or not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=path.name)


@router.delete("/reports/{report_id}")
def delete_report(report_id: int) -> dict:
    entry = Library().delete(report_id)
    return {"deleted": service.entry_dict(entry) if entry else None}


@router.post("/reports/{report_id}/ask")
async def ask(report_id: int, request: QuestionRequest) -> dict:
    return await service.ask(_entry(report_id), request.question)


@router.post("/reports/{report_id}/followups")
async def followups(report_id: int) -> dict:
    return {"topics": await service.followups(_entry(report_id))}


@router.post("/clarify")
async def clarify(request: ClarifyRequest) -> dict:
    return await service.clarify(request.query)


@router.post("/refine")
async def refine(request: RefineRequest) -> dict:
    return await service.refine(request.query, request.answers)


# blocklist
@router.get("/blocklist")
def get_blocklist() -> dict:
    return {"domains": blocklist.blocked_domains()}


@router.post("/blocklist")
def add_to_blocklist(request: DomainRequest) -> dict:
    return {"domain": blocklist.block(request.domain)}


@router.delete("/blocklist/{domain}")
def remove_from_blocklist(domain: str) -> dict:
    return {"removed": blocklist.unblock(domain)}
