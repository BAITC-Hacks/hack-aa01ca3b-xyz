"""Persistent register of assignments across meetings (a local JSON file, no external services)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from typing import Any

from app.core.config import DATA_DIR

REGISTRY_FILE = DATA_DIR / "registry" / "tasks.json"
STATUSES = ("progress", "done", "failed")  # в процессе / выполнено / не выполнено
_LOCK = threading.Lock()


def load() -> list[dict[str, Any]]:
    try:
        tasks = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return tasks if isinstance(tasks, list) else []


def _save(tasks: list[dict[str, Any]]) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(REGISTRY_FILE)


def task_key(action: dict[str, Any], meeting_title: str, meeting_date: str) -> str:
    return "|".join((meeting_date, meeting_title.strip().lower(), str(action.get("task", "")).strip().lower(), str(action.get("assignee", "")).strip().lower()))


def _overdue(deadline_iso: str) -> bool:
    try:
        return bool(deadline_iso) and date.fromisoformat(deadline_iso) < date.today()
    except ValueError:
        return False


def initial_status(action: dict[str, Any]) -> str:
    if str(action.get("status", "")) in {"Выполнено", "done"}:
        return "done"
    return "failed" if _overdue(str(action.get("deadline_iso", ""))) else "progress"


def effective_status(task: dict[str, Any]) -> str:
    """An unfinished task past its deadline is shown as not done."""
    status = str(task.get("status", "progress"))
    if status == "progress" and _overdue(str(task.get("deadline_iso", ""))):
        return "failed"
    return status if status in STATUSES else "progress"


def contains(action: dict[str, Any], meeting_title: str, meeting_date: str) -> bool:
    key = task_key(action, meeting_title, meeting_date)
    return any(task.get("key") == key for task in load())


def add(action: dict[str, Any], meeting_title: str, meeting_date: str) -> bool:
    """Add an assignment to the register; False when it is already there."""
    key = task_key(action, meeting_title, meeting_date)
    with _LOCK:
        tasks = load()
        if any(task.get("key") == key for task in tasks):
            return False
        tasks.append({
            "id": uuid.uuid4().hex[:8],
            "key": key,
            "task": str(action.get("task", "")),
            "assignee": str(action.get("assignee", "")),
            "deadline": str(action.get("deadline", "")),
            "deadline_iso": str(action.get("deadline_iso", "")),
            "assigned_by": str(action.get("assigned_by", "")),
            "quote": str(action.get("source_quote", "")),
            "meeting": meeting_title,
            "meeting_date": meeting_date,
            "status": initial_status(action),
            "added": datetime.now().isoformat(timespec="minutes"),
        })
        _save(tasks)
        return True


def set_status(task_id: str, status: str) -> None:
    if status not in STATUSES:
        return
    with _LOCK:
        tasks = load()
        for task in tasks:
            if task.get("id") == task_id:
                task["status"] = status
        _save(tasks)


def remove(task_id: str) -> None:
    with _LOCK:
        _save([task for task in load() if task.get("id") != task_id])
