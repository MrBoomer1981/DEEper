"""Reading the sources. Each kind of source has a module with `async fetch(source, client) -> Fetched`.

A post keeps the addresses it links to, as keys (`link_key()`): without the scheme, `www.`, tracking parameters
and the site's own pages. At stage 4 two posts that link to the same page are the same story.
"""

import re
import warnings
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)  # a summary that is only a URL is fine

TEXT_CHARS = 4000  # enough for an embedding and a summary; the rest of a long post adds nothing to either
# Tracking and referral parameters: Substack adds ?r=, X ?s= and ?t=, YouTube ?si= and ?feature=.
TRACKING = re.compile(r"^(utm_\w+|ref|ref_src|fbclid|gclid|mc_cid|mc_eid|igshid|si|source|r|s|t|feature|triedRedirect|isFreemail|publication_id|post_id)$")
SHARE = re.compile(r"/(intent|sharer?|submit)\b|/share\?")  # share buttons carry the post's own address
ARXIV = re.compile(r"^arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$")


@dataclass
class Post:
    external_id: str
    url: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)  # link_key() of the pages the post links to
    published: float | None = None  # seconds since the epoch; None when the source gives no date


@dataclass
class Fetched:
    posts: list[Post]
    etag: str | None = None  # for the next conditional GET
    modified: str | None = None


def clean_url(url: str) -> str:
    """The address without its fragment and tracking parameters, still clickable."""
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def link_key(url: str) -> str:
    """What two addresses of one page share: host without www. and path without the trailing slash; arXiv's
    abs, pdf and html pages of a paper, in any version, are one key."""
    parts = urlsplit(clean_url(url))
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    key = host + (parts.path.rstrip("/") or "") + (f"?{parts.query}" if parts.query else "")
    if match := ARXIV.match(key):
        return f"arxiv.org/abs/{match.group(1)}"
    return key


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def text_and_links(html: str, own_host: str) -> tuple[str, list[str]]:
    """Plain text of a post's HTML, and the keys of the pages it links to, other than its own site's."""
    soup = BeautifulSoup(html or "", "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.startswith(("http://", "https://")) or SHARE.search(href):
            continue
        key = link_key(href)
        if key.split("/", 1)[0] != own_host and key not in links:
            links.append(key)
    return " ".join(soup.get_text(" ").split())[:TEXT_CHARS], links[:30]


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    from my_browser.news.collectors import hf_papers, rss, telegram

    collectors = {"rss": rss.fetch, "hf_papers": hf_papers.fetch, "telegram": telegram.fetch}
    if source["kind"] not in collectors:
        raise ValueError(f"no collector for {source['kind']!r} yet")
    return await collectors[source["kind"]](source, client)
