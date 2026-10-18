"""Questions and answers about a report, kept next to it as <name>.qa.json (the web app and the bot share them)."""

import json
from datetime import datetime
from pathlib import Path


def qa_path(md_path: Path) -> Path:
    return md_path.with_suffix(".qa.json")


def load_qa(md_path: Path) -> list[dict]:
    path = qa_path(md_path)
    return json.loads(path.read_text("utf-8")) if path.exists() else []


def append(md_path: Path, question: str, answer: dict) -> dict:
    """Store the worker's answer ({a, cost_rub, research?}) and return the stored item."""
    item = {"q": question, "a": answer["a"], "at": datetime.now().isoformat(timespec="seconds"),
            "cost_rub": answer.get("cost_rub")}
    if answer.get("research"):
        item["research"] = answer["research"]
    qa_path(md_path).write_text(json.dumps(load_qa(md_path) + [item], ensure_ascii=False, indent=2), "utf-8")
    return item
