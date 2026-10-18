"""The search part's API: source selection and page reading for the worker's research.

In the all-in-one process the worker calls this app in-process; on its own it runs with
`uv run python -m my_browser.search` (SEARCH_PORT, default 8503).
"""

from collections import Counter

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from my_browser.common import costs
from my_browser.common.auth import require_token
from my_browser.search import reader, sources

app = FastAPI(title="DEEper search", dependencies=[Depends(require_token)])


class Candidate(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""


class SelectRequest(BaseModel):
    query: str
    candidates: list[Candidate]
    domain_counts: dict[str, int] = {}  # sources already taken per domain in this research
    blocked: list[str] = []


class ReadRequest(BaseModel):
    url: str


@app.post("/select")
async def select(request: SelectRequest) -> dict:
    """[(url, score)] best first, and the rating's token usage for the research's cost."""
    with costs.metered() as meter:
        selected = await sources.rank([c.model_dump() for c in request.candidates], request.query,
                                      Counter(request.domain_counts), request.blocked)
    return {"selected": selected, "usage": meter.by_model}


@app.post("/read")
async def read(request: ReadRequest) -> dict:
    """The page as gpt-researcher's scraper returns it; raw_content is None when nothing could read it."""
    return await reader.read(request.url)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
