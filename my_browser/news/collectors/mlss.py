"""Machine learning summer schools from awesome-mlss (github.com/awesome-mlss/awesome-mlss), a list kept by its
community: its data file gives each school's deadline with the time zone, its dates, place, topics and link. One post
per school whose deadline has not passed; a changed deadline is a new post (the id carries it), which joins the
school's story by its link, so the deadline is read again.

PyYAML comes with gpt-researcher, which the news group already needs.
"""

import asyncio
import time
from datetime import date, datetime

import httpx
import yaml

from my_browser.news.collectors import Fetched, Post, clean_url, link_key


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    response = await client.get(source["url"])
    response.raise_for_status()
    schools = await asyncio.to_thread(yaml.safe_load, response.text)
    return Fetched([post for post in map(_post, schools if isinstance(schools, list) else []) if post])


def _passed(value) -> bool:
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value)[:10])
        except ValueError:
            return False  # «rolling», empty: not passed
    return day < date.today()


def _post(school) -> Post | None:
    if not isinstance(school, dict) or not school.get("id") or not school.get("link"):
        return None
    deadline = school.get("deadline")
    if deadline and _passed(deadline):
        return None
    url = clean_url(str(school["link"]))
    topics = ", ".join(map(str, school.get("sub") or []))
    facts = [f"{school.get('full_name') or school.get('title')}.",
             f"Application deadline: {deadline} ({school.get('timezone') or 'time zone not given'})." if deadline else "",
             f"Dates: {school.get('date')}." if school.get("date") else "",
             f"Place: {school.get('place')}." if school.get("place") else "",
             f"Topics: {topics}." if topics else "", str(school.get("note") or ""), str(school.get("description") or "")]
    return Post(f"{school['id']}@{deadline}", url, " ".join(str(school.get("title") or url).split()),
                " ".join(" ".join(f.split()) for f in facts if f), [link_key(url)], time.time())
