"""Devpost's online hackathons, through the JSON its own pages read (/api/hackathons): one post per hackathon.

Only online ones: an in-person hackathon at an American university is not one to apply to from Russia. The list
holds the open and upcoming ones, 81 on 25.10.2026, nine a page. Devpost gives no date of announcement, so a
hackathon counts from when it was first seen; a known one is never stored again.
"""

import time

import httpx
from bs4 import BeautifulSoup

from my_browser.news.collectors import Fetched, Post, clean_url, link_key

MAX_PAGES = 20


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    posts = []
    for page in range(1, MAX_PAGES + 1):
        response = await client.get(source["url"], headers={"Accept": "application/json"}, params=[
            ("status[]", "upcoming"), ("status[]", "open"), ("challenge_type[]", "online"), ("order_by", "deadline"),
            ("page", page)])
        response.raise_for_status()
        data = response.json()
        batch = data.get("hackathons") or []
        posts += [post for post in map(_post, batch) if post]
        if len(batch) < ((data.get("meta") or {}).get("per_page") or 9):
            break
    return Fetched(posts)


def _post(hackathon: dict) -> Post | None:
    if not hackathon.get("id") or not hackathon.get("url"):
        return None
    url = clean_url(hackathon["url"])
    prize = BeautifulSoup(hackathon.get("prize_amount") or "", "html.parser").get_text().strip()
    themes = ", ".join(t.get("name", "") for t in hackathon.get("themes") or [])
    facts = [f"Online hackathon on Devpost by {hackathon.get('organization_name') or 'unknown organizer'}.",
             f"Submission period: {hackathon.get('submission_period_dates')}.",
             f"Status: {hackathon.get('open_state')}, {hackathon.get('time_left_to_submission') or ''}.",
             f"Prizes: {prize}." if prize else "", f"Themes: {themes}." if themes else "",
             f"Registered: {hackathon.get('registrations_count')}.",
             "Invite only." if hackathon.get("invite_only") else ""]
    return Post(str(hackathon["id"]), url, " ".join((hackathon.get("title") or url).split()),
                " ".join(f for f in facts if f), [link_key(url)], time.time())
