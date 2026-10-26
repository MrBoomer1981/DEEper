"""The report's text while the writer model writes it, for «Читать, пока пишется» (core's job page shows it).

gpt-researcher streams every call that writes report text (stream=True) and, with no websocket, only prints the
paragraphs when verbose. Inside writing(), the paragraphs go to the run's Draft instead: the job loop sets
`current` per job, and pipeline.research() and the detailed report mark the calls that write the report itself,
not its draft titles. Each streamed call is one part of the draft; a call that fails loses its part, since
upstream tries again. Importing this module patches gpt-researcher's provider in this process.
"""

import contextvars
from collections.abc import Callable
from contextlib import contextmanager

from gpt_researcher.llm_provider.generic.base import GenericLLMProvider


class Draft:
    """The parts written so far and the one being written; `on_change` is called after each paragraph."""

    def __init__(self, on_change: Callable[[], None] = lambda: None):
        self.parts: list[str] = []
        self.part = ""
        self.on_change = on_change

    def text(self) -> str:
        return "\n\n".join(p.strip() for p in (*self.parts, self.part) if p.strip())

    def _begin(self) -> None:
        self.part = ""

    def _add(self, paragraph: str) -> None:
        self.part += paragraph
        self.on_change()

    def _end(self, response: str) -> None:
        self.parts.append(response)
        self.part = ""
        self.on_change()

    def _drop(self) -> None:
        self.part = ""
        self.on_change()


current: contextvars.ContextVar[Draft | None] = contextvars.ContextVar("draft", default=None)
_writing: contextvars.ContextVar[bool] = contextvars.ContextVar("draft_writing", default=False)


@contextmanager
def writing():
    """The streamed calls inside write the report's text (in a task of its own when they run beside others)."""
    token = _writing.set(True)
    try:
        yield
    finally:
        _writing.reset(token)


def _draft() -> Draft | None:
    return current.get() if _writing.get() else None


_stream_response = GenericLLMProvider.stream_response
_send_output = GenericLLMProvider._send_output


async def _stream_response_drafted(self, messages, websocket=None, **kwargs):
    draft = _draft()
    if draft is None:
        return await _stream_response(self, messages, websocket, **kwargs)
    draft._begin()
    try:
        response = await _stream_response(self, messages, websocket, **kwargs)
    except BaseException:
        draft._drop()
        raise
    draft._end(response)
    return response


async def _send_output_drafted(self, content, websocket=None):
    draft = _draft()
    if draft is not None and websocket is None:
        draft._add(content)
        return
    await _send_output(self, content, websocket)


GenericLLMProvider.stream_response = _stream_response_drafted
GenericLLMProvider._send_output = _send_output_drafted
