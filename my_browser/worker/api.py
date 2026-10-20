"""The worker part's API: LLM helpers around reports, called by core (questions, follow-ups, clarification,
short versions).

In the all-in-one process core calls this app in-process; on its own it runs with
`uv run python -m my_browser.worker` (WORKER_PORT, default 8502) together with the job loop.
"""

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from my_browser.common import costs
from my_browser.common.auth import require_token
from my_browser.worker import ask, shorten, suggest

app = FastAPI(title="DEEper worker", dependencies=[Depends(require_token)])


class AskRequest(BaseModel):
    markdown: str
    question: str


class FollowupsRequest(BaseModel):
    query: str
    markdown: str


class ClarifyRequest(BaseModel):
    query: str


class RefineRequest(BaseModel):
    query: str
    answers: list[tuple[str, str]]


class ShortenRequest(BaseModel):
    markdown: str
    words: int = shorten.SHORT_WORDS


@app.post("/ask")
async def answer(request: AskRequest) -> dict:
    """{a, cost_rub, research?}: the answer from the report's text only."""
    return await ask.answer(request.markdown, request.question)


@app.post("/followups")
async def followups(request: FollowupsRequest) -> dict:
    with costs.metered() as meter:
        topics = await suggest.followups(request.query, request.markdown)
    return {"topics": topics, "cost_rub": round(meter.rub(), 2)}


@app.post("/clarify")
async def clarify(request: ClarifyRequest) -> dict:
    questions, rub = await suggest.questions(request.query)
    return {"questions": questions, "cost_rub": round(rub, 3)}


@app.post("/refine")
async def refine(request: RefineRequest) -> dict:
    topic, rub = await suggest.refine(request.query, request.answers)
    return {"topic": topic, "cost_rub": round(rub, 3)}


@app.post("/shorten")
async def shorten_report(request: ShortenRequest) -> dict:
    """{markdown, words, cost_rub}: the report's short version with the list of the sources it cites."""
    return await shorten.shorten(request.markdown, request.words)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
