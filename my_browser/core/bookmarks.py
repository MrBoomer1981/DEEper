"""Bookmarks: passages the user saved from reports (the report screen's «Сохранить»), kept in
REPORTS_DIR/bookmarks.json. Each keeps the report's file name too, because library ids change when the
database is rebuilt; Library.sync() ignores this file (it is not named like a report)."""

import json
import threading
import uuid
from datetime import datetime

from my_browser.common.settings import REPORTS_DIR

PATH = REPORTS_DIR / "bookmarks.json"
_lock = threading.Lock()


def load() -> list[dict]:
    """Newest first: [{id, report_id, report, text, section, created, folder}]."""
    try:
        items = json.loads(PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(items, key=lambda b: b.get("created", ""), reverse=True)


def add(report_id: int, report: str, text: str, section: str = "", folder: str = "") -> dict:
    item = {"id": uuid.uuid4().hex[:10], "report_id": report_id, "report": report, "text": " ".join(text.split())[:2000],
            "section": section[:200], "created": datetime.now().isoformat(timespec="seconds"), "folder": folder}
    with _lock:
        items = load()
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps([item, *items], ensure_ascii=False, indent=1), "utf-8")
    return item


def delete(bookmark_id: str) -> bool:
    with _lock:
        items = load()
        kept = [b for b in items if b["id"] != bookmark_id]
        if len(kept) == len(items):
            return False
        PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=1), "utf-8")
        return True
