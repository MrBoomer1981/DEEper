"""Fact-check: every sentence that cites a source is compared with that page as the research read it.

The research keeps the text of every page it read (hooks.read_pages), so nothing is downloaded again.
For each sentence with a link (a claim) the passages of the cited page most like the claim are found
by BM25 over the claim and its English translation (the report is Russian, most sources English)
plus the numbers they share. FAST_LLM judges the claims citing one page in one call, over the
passages they need together. A claim its page does not support is fixed where the research has the
evidence:
- another page the research read says it: the link moves there (or the extra link goes, when the
  sentence already cites that page);
- the cited page gives other numbers, dates, names or facts: the writer model corrects just those;
- otherwise it stays as written and is listed as doubtful.
A conclusion the report draws from what its source says counts as supported: the check is after wrong
facts and wrong links, not after the report's own reasoning. eval/run.py judges citations with
claims(), Passages and translate() as well.
"""

import asyncio
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from my_browser.common import costs
from my_browser.common.report import FACTCHECK_TITLE, VERDICT_TEXT, body
from my_browser.common.text import MARKDOWN_LINK, host, plural, readable_url
from my_browser.worker.stemming import tokenize
from my_browser.worker.suggest import json_list

logger = logging.getLogger(__name__)

MAX_CLAIMS = 150  # a detailed report links 100-200 sentences; past this, sentences with numbers go first
EXCERPTS = 4  # chunks per claim
BATCH_CLAIMS, BATCH_CHUNKS = 8, 10  # claims and chunks of one page judged in one call
CHUNK_WORDS, CHUNK_STEP = 120, 100
JUDGING, REWRITING = 8, 4  # calls at once

LINK = MARKDOWN_LINK
REFERENCES = re.compile(r"^#{1,3}\s*(?:список\s+)?(?:источник|литератур|ссылк|reference|bibliograph)", re.I | re.M)
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[«\"(\[A-ZА-ЯЁ0-9])")
LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
CITATION_WORDS = {"источник", "источники", "по", "данным", "см", "также", "и", "source", "sources", "see", "also", "and"}
NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?:[^\S\n]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
ANSWER_LINE = re.compile(r"^[^\w\n]*(\d+)[^\w\n]+([A-Z_]+)\b[ \t|:*—–-]*(.*)$", re.M)  # «2. MISMATCH | note», «2) **MISMATCH** — note»
VERDICTS = ("SUPPORTED", "PARTIAL", "MISMATCH", "CONTRADICTED", "NOT_FOUND", "NO_TEXT")
CHECKED = ("SUPPORTED", "PARTIAL", "MISMATCH", "CONTRADICTED", "NOT_FOUND")
FIXABLE = ("MISMATCH", "CONTRADICTED")  # the page says something else: correct the sentence
# Listed for the reader when not fixed. PARTIAL is only counted: the main point is there, and a sentence
# drawing on several sources gets it for each of them.
DOUBTFUL = ("MISMATCH", "CONTRADICTED", "NOT_FOUND")

TRANSLATE_PROMPT = """Translate each numbered statement into English. Keep numbers, units, dates and names exactly.
Return ONLY a JSON array of {count} strings in the same order.

{listing}"""

JUDGE_PROMPT = """You check statements from a research report against excerpts from the page they cite. The statements may be in Russian while the page is in English.

Verdicts:
SUPPORTED - the excerpts say this. Paraphrase, translation and rounding are fine, and so is a conclusion the report draws from what the excerpts say.
PARTIAL - the excerpts support the main point, but a detail or a claim the statement adds is not in them.
MISMATCH - the excerpts are about the same thing but give a different number, date, name or fact.
CONTRADICTED - the excerpts state the opposite.
NOT_FOUND - the excerpts do not say this.
NO_TEXT - the excerpts are not the article: a paywall, cookie banner, error page or menu.

A statement may come after the sentence it follows in the report («Before:»), given only so that words like «их» or «эта компания» make sense; that sentence is not checked. A statement citing several sources says which citation is this page («Cited as:»): judge only the part of the statement that citation stands for.

Answer every statement on its own line: its number, the verdict and, unless SUPPORTED, " | " with one short sentence in Russian on what the page says instead. For example:
1. SUPPORTED
2. MISMATCH | Страница называет 12%, а не 15%.

Excerpts from {url}:
{excerpts}

Statements:
{statements}"""

REWRITE_PROMPT = """Ты редактор исследовательского отчёта. Проверка нашла, что в предложении число, дата, имя или факт расходятся с источником, на который оно ссылается. Исправь в предложении только это расхождение по выдержкам из источника; остальное, включая выводы автора и Markdown-ссылки, оставь как есть. Ничего не добавляй от себя. Ответь только исправленным предложением, без кавычек и пояснений.

Что нашла проверка: {note}

Предыдущее предложение отчёта (для понимания, его не меняй): {context}

Предложение:
{sentence}

Выдержки из источника {url}:
{excerpts}"""


@dataclass
class Claim:
    statement: str  # the sentence as plain text, links replaced by their anchors
    url: str  # one source the sentence links
    raw: str  # the sentence as written in the Markdown
    context: str = ""  # the sentence before it in the paragraph, as plain text: what «их» refers to
    cited_as: str = ""  # this link's anchor, when the sentence cites several sources
    verdict: str = ""  # one of VERDICTS, UNREAD (the research did not read the page) or "" (no answer)
    note: str = ""  # what the page says instead
    fix: str = ""  # relink | rewrite
    new_url: str = ""  # relink: the page that says it


def text_and_references(markdown: str) -> tuple[str, str]:
    """The report's text and its reference list (from the last references heading), appended sections dropped."""
    report = body(markdown)
    headings = list(REFERENCES.finditer(report))
    if not headings:
        return report, ""
    return report[:headings[-1].start()], report[headings[-1].start():]


def units(text: str) -> list[str]:
    """Paragraphs of the text, with every list item as its own unit; headings and tables skipped."""
    found = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or lines[0].lstrip().startswith(("#", "|")):
            continue
        if all(LIST_ITEM.match(line) for line in lines):
            found += [" ".join(line.split()) for line in lines]
        else:
            found.append(" ".join(block.split()))
    return found


def _citation_only(sentence: str) -> bool:
    """Links with next to no words of their own: «([IEA](…)).», «Источники: [A](…), [B](…).», «Данные из: [A](…); [B](…).».
    An anchor long enough to be a claim itself («[Toyota начнёт выпуск в 2027 году](…)») is not one."""
    links = list(LINK.finditer(sentence))
    words = re.findall(r"[^\W\d_]{2,}", LINK.sub("", sentence).lower())
    bare = all(w in CITATION_WORDS for w in words) or (len(links) > 1 and len(words) <= 4)
    return bare and all(len(m.group(1)) <= 40 for m in links)


def claims(markdown: str) -> list[Claim]:
    """Every (sentence, link) in the report's text. A single citation standing after the period, as in
    «… в 2025 году. ([IEA](…))», counts for the sentence before it; a bare list of sources counts for nothing."""
    text, _ = text_and_references(markdown)
    found, seen = [], set()
    for unit in units(text):
        previous, before = None, ""  # the last sentence long enough to check; the plain sentence before this one
        for sentence in SENTENCE_END.split(unit):
            links = list(dict.fromkeys(m.group(2) for m in LINK.finditer(sentence)))
            plain = " ".join(LINK.sub(r"\1", sentence).replace("**", "").split()).lstrip("-*+ ")
            if links and _citation_only(sentence):
                if len(links) > 1 or not previous:
                    continue
                statement, raw, context = previous[0], f"{previous[1]} {sentence}", previous[2]
            elif len(plain) >= 40:
                statement, raw, context = plain, sentence, before
                previous = (plain, sentence, before)
            else:
                before = plain or before
                continue
            before = statement
            anchors = {m.group(2): m.group(1) for m in reversed(list(LINK.finditer(raw)))}  # the first anchor of each url
            for url in links:
                if (statement, url) not in seen:
                    seen.add((statement, url))
                    found.append(Claim(statement, url, raw, _short(context, 300), anchors[url] if len(anchors) > 1 else ""))
    return found


def numbers(text: str) -> set[str]:
    """Numbers written either way, normalized: «1 500» and «1,500» -> 1500, «2,5» and «2.50» -> 2.5.
    Lone digits match too much and are skipped."""
    found = set()
    for match in NUMBER.finditer(text):
        n = re.sub(r"\s", "", match.group(0))
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", n):  # English thousands
            n = n.replace(",", "")
        n = n.replace(",", ".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        if len(n.replace(".", "")) > 1:
            found.add(n)
    return found


def page_key(url: str) -> str:
    """One page however the writer copied its address: without scheme, www, fragment or final slash."""
    parsed = urlparse(readable_url(url.strip()))
    return host(url) + parsed.path.rstrip("/") + (f"?{parsed.query}" if parsed.query else "")


@dataclass
class _Chunk:
    text: str
    terms: Counter
    numbers: set


class Passages:
    """Pages split into overlapping chunks, ranked for a claim by BM25 over the words of the claim and
    its English translation, plus the numbers it shares with a chunk (strong evidence in any language)."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.urls: dict[str, str] = {}  # page_key -> the url the page was read under
        self.chunks: dict[str, list[_Chunk]] = {}
        df: Counter = Counter()
        for url, text in pages.items():
            words = (text or "").split()[:30000]
            chunks = []
            for start in range(0, len(words), CHUNK_STEP):
                piece = " ".join(words[start:start + CHUNK_WORDS])
                chunks.append(_Chunk(piece, Counter(tokenize(piece)), numbers(piece)))
                if start + CHUNK_WORDS >= len(words):
                    break
            if chunks:
                self.urls[page_key(url)] = url
                self.chunks[url] = chunks
                for chunk in chunks:
                    df.update(chunk.terms.keys())
        all_chunks = [chunk for chunks in self.chunks.values() for chunk in chunks]
        self.avg_len = sum(sum(c.terms.values()) for c in all_chunks) / max(len(all_chunks), 1) or 1.0
        self.idf = {t: math.log((len(all_chunks) - n + 0.5) / (n + 0.5) + 1) for t, n in df.items()}

    @staticmethod
    def query(statement: str, translation: str = "") -> tuple[set[str], set[str]]:
        return set(tokenize(f"{statement} {translation}")), numbers(statement)

    def find(self, url: str) -> str | None:
        """The url the cited page was read under, or None when the research did not read it."""
        return self.urls.get(page_key(url))

    def _score(self, chunk: _Chunk, query: tuple[set[str], set[str]]) -> float:
        terms, nums = query
        length = sum(chunk.terms.values())
        score = 0.0
        for term in terms:
            if tf := chunk.terms.get(term):
                score += self.idf.get(term, 0.0) * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / self.avg_len))
        return score + 3 * len(nums & chunk.numbers)

    def best(self, url: str, query: tuple[set[str], set[str]], count: int = EXCERPTS) -> list[int]:
        """Indexes of the page's chunks most like the claim."""
        chunks = self.chunks.get(url) or []
        return sorted(range(len(chunks)), key=lambda i: self._score(chunks[i], query), reverse=True)[:count]

    def texts(self, url: str, indexes) -> list[str]:
        return [self.chunks[url][i].text for i in sorted(indexes)]

    def excerpts(self, url: str, query: tuple[set[str], set[str]], count: int = EXCERPTS) -> list[str]:
        """The page's chunks most like the claim, in page order."""
        return self.texts(url, self.best(url, query, count))

    def best_pages(self, query: tuple[set[str], set[str]], exclude: set[str], count: int = 2) -> list[str]:
        """Other pages whose best chunk is most like the claim."""
        scored = [(max(self._score(c, query) for c in chunks), url) for url, chunks in self.chunks.items() if url not in exclude]
        return [url for score, url in sorted(scored, reverse=True)[:count] if score > 0]


async def complete(cfg, prompt: str, max_tokens: int, smart: bool = False) -> str:
    """One call to FAST_LLM, or with smart to SMART_LLM (the writer) of the run's config."""
    from gpt_researcher.utils.llm import create_chat_completion

    return await create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=cfg.smart_llm_model if smart else cfg.fast_llm_model,
        llm_provider=cfg.smart_llm_provider if smart else cfg.fast_llm_provider,
        temperature=0.0,
        max_tokens=max_tokens,
        llm_kwargs=cfg.llm_kwargs,
    )


async def translate(statements: list[str], cfg, batch: int = 30) -> list[str]:
    """English versions for the passage search; "" where a batch failed (the claim's own words still count)."""
    async def one(part: list[str]) -> list[str]:
        listing = "\n".join(f"{i}. {s}" for i, s in enumerate(part, 1))
        try:
            items = json_list(await complete(cfg, TRANSLATE_PROMPT.format(count=len(part), listing=listing), 120 * len(part)))
            if len(items) == len(part):
                return [str(item) for item in items]
            logger.warning("Translation returned %d items for %d statements", len(items), len(part))
        except Exception as e:
            logger.warning("Translation failed: %s", e)
        return [""] * len(part)

    parts = await asyncio.gather(*(one(statements[i:i + batch]) for i in range(0, len(statements), batch)))
    return [text for part in parts for text in part]


async def judge(cfg, url: str, excerpts: list[str], claims_: list[Claim]) -> list[tuple[str, str]]:
    """(verdict, note) for each claim, judged on one page's excerpts; ("", "") where the answer has none."""
    def item(i: int, c: Claim) -> str:
        lines = ([f"Before: {c.context}"] if c.context else []) + ([f"Cited as: {c.cited_as}"] if c.cited_as else [])
        return f"{i}. " + "".join(f"{line}\n   " for line in lines) + ("Statement: " if lines else "") + c.statement

    statements = "\n".join(item(i, c) for i, c in enumerate(claims_, 1))
    prompt = JUDGE_PROMPT.format(url=url, excerpts="\n\n".join(f"[{i}] {e}" for i, e in enumerate(excerpts, 1)),
                                 statements=statements)
    answer = await complete(cfg, prompt, 80 + 70 * len(claims_))
    verdicts = {}
    for match in ANSWER_LINE.finditer(answer):
        if match.group(2) in VERDICTS:
            verdicts.setdefault(int(match.group(1)), (match.group(2), (match.group(3) or "").strip()))
    return [verdicts.get(i, ("", "")) for i in range(1, len(claims_) + 1)]


def batches(claims_of_page: list[tuple["Claim", list[int]]]) -> list[tuple[list["Claim"], set[int]]]:
    """Claims of one page in groups sharing at most BATCH_CHUNKS chunks, neighbours on the page together."""
    groups, current, chunks = [], [], set()
    for claim, best in sorted(claims_of_page, key=lambda item: min(item[1], default=0)):
        if current and (len(current) >= BATCH_CLAIMS or len(chunks | set(best)) > BATCH_CHUNKS):
            groups.append((current, chunks))
            current, chunks = [], set()
        current.append(claim)
        chunks |= set(best)
    if current:
        groups.append((current, chunks))
    return groups


def _tidy(sentence: str) -> str:
    """Punctuation left behind by a removed link: «(; [B](…))», «()», « .»."""
    sentence = re.sub(r"\(\s*[;,]\s*", "(", sentence)
    sentence = re.sub(r"\s*[;,]\s*\)", ")", sentence)
    sentence = re.sub(r"\s*\(\s*\)", "", sentence)
    sentence = re.sub(r"\s+([.,;:!?])", r"\1", sentence)
    return re.sub(r"\s{2,}", " ", sentence).strip()


def relink(sentence: str, old_url: str, new_url: str, title: str = "") -> str:
    """The sentence citing new_url instead of old_url; when it already cites new_url, old_url's link goes."""
    link = re.compile(r"\[([^\]]*)\]\(" + re.escape(old_url) + r"\)")
    if f"]({new_url})" in sentence:
        return _tidy(link.sub("", sentence, count=1))
    anchor = (title if title and len(title) <= 70 else host(new_url)).replace("[", "(").replace("]", ")")
    return link.sub(lambda _: f"[{anchor}]({new_url})", sentence, count=1)


def replace_sentence(markdown: str, old: str, new: str) -> tuple[str, bool]:
    """Replace the first occurrence of a sentence, whatever whitespace the Markdown has inside it."""
    pattern = r"\s+".join(map(re.escape, old.split()))
    result, count = re.subn(pattern, lambda _: new, markdown, count=1)
    return result, bool(count)


async def rewrite(cfg, sentence: str, claim: Claim, excerpts: list[str]) -> str | None:
    """The sentence with what differs from its source corrected by the writer model, or None if the answer looks wrong."""
    marker = LIST_ITEM.match(sentence)
    prefix, text = (sentence[:marker.end()], sentence[marker.end():]) if marker else ("", sentence)
    listing = "\n\n".join(f"[{i}] {e}" for i, e in enumerate(excerpts, 1))
    answer = await complete(cfg, REWRITE_PROMPT.format(note=claim.note or claim.verdict, context=claim.context or "—",
                                                       sentence=text, url=claim.url, excerpts=listing), 600, smart=True)
    new = answer.strip().strip("«»\"").strip()
    if " ".join(new.split()) == " ".join(text.split()):  # the writer model saw nothing to correct
        logger.info("Rewrite left the sentence as it was: %s", claim.url)
        return None
    kept_links = all(m.group(2) in new for m in LINK.finditer(text))
    if not new or "\n" in new or not kept_links or not 0.5 * len(text) <= len(new) <= 2 * len(text) + 100:
        logger.info("Rewrite rejected for %s: %r", claim.url, new[:200])
        return None
    return prefix + new


async def run(markdown: str, pages: dict[str, dict], config_path, on_progress=None) -> tuple[str, dict]:
    """The report with what its sources do not support fixed where possible, and the check's summary.
    pages: url -> {"text", "title"} of every page the research read. A failure leaves the report as it was."""
    from gpt_researcher.config import Config

    started, outer = time.monotonic(), costs.current.get()
    with costs.metered() as meter:  # counted apart, then added to the research's cost
        try:
            fixed, found = await _check(markdown, pages, Config(str(config_path)), on_progress or (lambda text: None))
            report = summary(found)
        except Exception as e:
            logger.exception("Fact-check failed")
            fixed, report = markdown, {"error": str(e)[:300]}
    if outer is not None:
        outer.merge(meter.by_model)
    report.update(cost_rub=round(meter.rub(), 2), seconds=round(time.monotonic() - started))
    return fixed, report


async def _check(markdown: str, pages: dict[str, dict], cfg, progress) -> tuple[str, list[Claim]]:
    found = claims(markdown)
    if len(found) > MAX_CLAIMS:
        keep = set(sorted(range(len(found)), key=lambda i: (not numbers(found[i].statement), i))[:MAX_CLAIMS])
        found = [claim for i, claim in enumerate(found) if i in keep]
    if not found:
        return markdown, []
    progress(f"Проверяю факты: {len(found)} {plural(len(found), 'утверждение', 'утверждения', 'утверждений')} со ссылками...")
    passages = Passages({url: page.get("text") or "" for url, page in pages.items()})
    titles = {url: page.get("title") or "" for url, page in pages.items()}
    translations = await translate([c.statement for c in found], cfg)
    queries = {id(c): Passages.query(c.statement, t) for c, t in zip(found, translations)}
    limit = asyncio.Semaphore(JUDGING)

    async def check(url: str, group: list[Claim], chunks: set[int]) -> None:
        async with limit:
            results = await judge(cfg, url, passages.texts(url, chunks), group)
        for claim, (verdict, note) in zip(group, results):
            claim.verdict, claim.note = verdict, note

    by_page: dict[str, list] = {}
    for claim in found:
        if page := passages.find(claim.url):
            by_page.setdefault(page, []).append((claim, passages.best(page, queries[id(claim)])))
        else:
            claim.verdict = "UNREAD"
    await asyncio.gather(*(check(url, group, chunks) for url, items in by_page.items() for group, chunks in batches(items)))

    async def look_elsewhere(claim: Claim) -> None:
        cited, query = passages.find(claim.url), queries[id(claim)]
        for url in passages.best_pages(query, exclude={cited} if cited else set()):
            if page_key(url) == page_key(claim.url):
                continue
            async with limit:
                [(verdict, _)] = await judge(cfg, url, passages.excerpts(url, query), [claim])
            if verdict == "SUPPORTED":
                claim.fix, claim.new_url = "relink", url
                return

    unsupported = [c for c in found if c.verdict in ("MISMATCH", "CONTRADICTED", "NOT_FOUND", "NO_TEXT", "UNREAD")]
    if unsupported:
        progress(f"Ищу подтверждение в других источниках: {len(unsupported)}...")
        await asyncio.gather(*(look_elsewhere(c) for c in unsupported))

    edited: dict[str, str] = {}  # sentence as written -> as fixed
    for claim in found:
        if claim.fix == "relink":
            current = edited.get(claim.raw, claim.raw)
            edited[claim.raw] = relink(current, claim.url, claim.new_url, titles.get(claim.new_url, ""))

    to_rewrite = {}  # one rewrite per sentence, from the first claim its page contradicts
    for claim in found:
        if not claim.fix and claim.verdict in FIXABLE and claim.raw not in to_rewrite:
            to_rewrite[claim.raw] = claim
    if to_rewrite:
        progress(f"Исправляю {len(to_rewrite)} {plural(len(to_rewrite), 'предложение', 'предложения', 'предложений')} по источникам...")
        writing = asyncio.Semaphore(REWRITING)

        async def fix(raw: str, claim: Claim) -> None:
            page = passages.find(claim.url)
            async with writing:
                try:
                    new = await rewrite(cfg, edited.get(raw, raw), claim, passages.excerpts(page, queries[id(claim)]))
                except Exception as e:
                    logger.warning("Rewrite failed for %s: %s", claim.url, e)
                    return
            if new:
                edited[raw], claim.fix = new, "rewrite"

        await asyncio.gather(*(fix(raw, claim) for raw, claim in to_rewrite.items()))

    for raw, new in edited.items():
        markdown, replaced = replace_sentence(markdown, raw, new)
        if not replaced:
            logger.info("Sentence not found for its fix: %s", raw[:120])
            for claim in found:
                if claim.raw == raw:
                    claim.fix = claim.new_url = ""
    return markdown, found


def summary(found: list[Claim]) -> dict:
    """Counts, the fixes and the claims still doubtful; for the report's JSON and section."""
    counts = Counter(c.verdict for c in found)
    checked = sum(counts[v] for v in CHECKED)

    def item(c: Claim) -> dict:
        # raw: the sentence as it stands in the report, which core marks; a fixed one is no longer there
        extra = {"fix": c.fix, "new_url": c.new_url} if c.fix else {"raw": c.raw}
        return {"statement": c.statement, "url": c.url, "verdict": c.verdict.lower(), "note": c.note, **extra}

    return {
        "claims": len(found),
        "checked": checked,
        "supported": counts["SUPPORTED"],
        "partial": counts["PARTIAL"],
        "mismatch": counts["MISMATCH"],
        "contradicted": counts["CONTRADICTED"],
        "not_found": counts["NOT_FOUND"],
        "unchecked": len(found) - checked,  # the page was not read, had no text, or the judge did not answer
        # the eval's scale: partial or differing details count half
        "accuracy": round((counts["SUPPORTED"] + 0.5 * (counts["PARTIAL"] + counts["MISMATCH"])) / checked, 3) if checked else None,
        "relinked": sum(c.fix == "relink" for c in found),
        "rewritten": sum(c.fix == "rewrite" for c in found),
        "fixed": [item(c) for c in found if c.fix],
        "doubtful": [item(c) for c in found if not c.fix and c.verdict in DOUBTFUL],
    }


def section(report: dict | None) -> str:
    """The «Проверка фактов» section for the report's files."""
    if not report or not report.get("checked"):
        return ""
    n = report["checked"]
    parts = [f"{label} {report[key]}" for key, label in (
        ("supported", "подтверждено"), ("partial", "частично"), ("mismatch", "с другими данными"),
        ("contradicted", "противоречит источнику"), ("not_found", "не найдено в источнике")) if report.get(key)]
    lines = [f"Проверено {n} {plural(n, 'утверждение', 'утверждения', 'утверждений')} со ссылками на источники: "
             + ", ".join(parts) + "."]
    if u := report["unchecked"]:
        lines[0] += (f" Ещё {u} {plural(u, 'ссылку', 'ссылки', 'ссылок')} проверить не удалось: "
                     "страницы нет среди прочитанных или в ней нет текста.")
    fixes = []
    if w := report["rewritten"]:
        fixes.append(f"{w} {plural(w, 'предложение исправлено', 'предложения исправлены', 'предложений исправлено')} по данным источника")
    if r := report["relinked"]:
        fixes.append(f"у {r} {plural(r, 'утверждения', 'утверждений', 'утверждений')} ссылка заменена на источник, где это сказано")
    if fixes:
        text = "; ".join(fixes)
        lines.append(text[0].upper() + text[1:] + ".")
    if report["doubtful"]:
        lines.append("Не подтверждены источником, проверьте сами:")
        lines.append("\n".join(
            f"- «{_short(d['statement'])}» — {VERDICT_TEXT.get(d['verdict'], d['verdict'])}"
            + (f": {_lower_first(d['note'].rstrip('.'))}" if d["note"] else "") + f" ([{host(d['url'])}]({d['url']}))."
            for d in report["doubtful"]
        ))
    return f"\n\n{FACTCHECK_TITLE}\n\n" + "\n\n".join(lines) + "\n"


def _lower_first(text: str) -> str:
    """«Страница говорит…» after a colon reads «страница говорит…»; «IEA сообщает…» stays as it is."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def _short(text: str, limit: int = 220) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
