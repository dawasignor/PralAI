"""The pinned session's transcript: one line per console turn.

The console opens the pinned session (core/pinned.py) and replays its
history from `sessions/<id>/transcript.jsonl`. Each line is what was asked
and what came back.

Checkpoints -- likes, frozen modules, rollback and lock-in -- were removed on
2026-11-23 (user decision): every turn runs as it did before them. Lines
written while they existed still carry `liked`, `rolled_back`, `files` and
similar keys; they are ignored. Their `checkpoints/` and `rolled_back/`
folders are left on disk, unread.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import pinned

_LOCK = threading.RLock()


def _path(session_id: str) -> Path:
    from . import session

    return session.folder(session_id) / "transcript.jsonl"


def turns(session_id: str) -> list[dict[str, Any]]:
    path = _path(session_id)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue                    # a torn last line is skipped, not fatal
    return out


def record_turn(session_id: str, *, task: str, reply: str, status: str, agent: str,
                persona: str, flows: list[str],
                events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Append one turn: what was asked and what came back."""
    with _LOCK:
        n = len(turns(session_id))
        entry = {"id": f"turn-{n + 1:03d}",
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "task": task, "reply": reply, "status": status, "agent": agent,
                 "persona": persona, "flows": list(flows), "events": events or []}
        path = _path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    return entry


def view(session_id: str) -> dict[str, Any]:
    """The transcript, for the console to replay."""
    return {"turns": [{"id": e["id"], "at": e.get("at", ""), "task": e.get("task", ""),
                       "reply": e.get("reply", ""), "status": e.get("status", "ok"),
                       "agent": e.get("agent", "argos"), "events": e.get("events") or []}
                      for e in turns(session_id)]}


def enabled(session_id: str) -> bool:
    """A transcript is kept for the pinned session only."""
    return pinned.is_pinned(session_id)
