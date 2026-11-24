"""Persistent journal: agents/<slug>/memory.md and memory.local.md.

The journal is human-readable, not a database. It has three purposes:
  * active state  -- what the agent believes right now, carried across sessions
  * debug notes   -- things that went wrong and what was tried
  * session log   -- append-only record of runs

memory.md holds the first two. Humans own it, it is tracked in git, and it is
re-injected into the agent's prompt. The session log is machine-appended to
memory.local.md beside it, which is gitignored: it records what the user typed,
word for word, so it stays on the machine that ran the agent.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .config import AGENTS_DIR, memory_enabled

SESSION_LOG_HEADING = "## Session Log"

# Match the heading only as a real heading line -- these files talk *about*
# `## Session Log` in their prose, and a naive str.split truncates on that.
_LOG_RE = re.compile(rf"^{re.escape(SESSION_LOG_HEADING)}\s*$", re.MULTILINE)


def _split_at_log(text: str) -> tuple[str, str | None]:
    """Return (everything before the session log, the log body or None)."""
    matches = list(_LOG_RE.finditer(text))
    if not matches:
        return text, None
    last = matches[-1]
    return text[: last.start()], text[last.end():]


def memory_path(agent_slug: str) -> Path:
    return AGENTS_DIR / agent_slug / "memory.md"


def log_path(agent_slug: str) -> Path:
    """The local, gitignored session log of one agent."""
    return AGENTS_DIR / agent_slug / "memory.local.md"


def read_memory(agent_slug: str) -> str:
    path = memory_path(agent_slug)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_active_state(agent_slug: str) -> str:
    """Return everything above the session log -- the part worth re-injecting
    into the prompt each run. The log itself is for humans and grows unbounded.
    """
    head, _ = _split_at_log(read_memory(agent_slug))
    return head.strip()


def append_entry(agent_slug: str, task: str, outcome: str,
                 notes: str = "") -> None:
    """Append one dated entry under `## Session Log` in memory.local.md.

    No-op if MEMORY_WRITE=0, or if the agent has no memory.md (no journal).
    """
    if not memory_enabled():
        return
    if not memory_path(agent_slug).exists():
        return
    path = log_path(agent_slug)
    if not path.exists():
        path.write_text(f"# {agent_slug} - session log (local, not tracked)\n\n"
                        f"{SESSION_LOG_HEADING}\n", encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n### {stamp}\n- **Task:** {task}\n- **Outcome:** {outcome}\n"
    if notes:
        entry += f"- **Notes:** {notes}\n"

    head, log = _split_at_log(path.read_text(encoding="utf-8"))
    if log is None:
        text = head.rstrip() + f"\n\n{SESSION_LOG_HEADING}\n{entry}"
    else:
        text = head + SESSION_LOG_HEADING + "\n" + log.rstrip() + "\n" + entry
    path.write_text(text, encoding="utf-8")
