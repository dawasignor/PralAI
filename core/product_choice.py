"""The flood-map product a session's Modules 9-10 map: the user's, or the best-ranked.

User decision (2026-11-18): the hazard-exposure-vulnerability modules map the
BEST-RANKED product by default, and another one only when the user
specifically asks for it ("use the MODIS flood map ..."). The choice is read
from the user's own message in Python -- a model's wording never sets it --
and kept in the session folder until the user names another product, asks
for the best-ranked one again, or the AOI or window changes.

Read narrowly on purpose: a product name alone ("compare GFM and MODIS against
the reference") is not a choice. It takes a product name AND a word asking to
use it, and exactly one product left after "instead of X" is set aside.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE = "flood_product.json"

# Product id (the MCDM alternative id) -> how a user names it.
NAMES: dict[str, str] = {
    "gfm": r"\bgfm\b|global flood monitoring",
    "mcdwd": r"\bmodis\b|\bmcdwd\b",
    "vcdwd": r"\bviirs\b|\bvcdwd\b",
    "dswx_hls": r"dswx[\s_-]*hls|opera[\s\w-]{0,12}\bhls\b",
    "dswx_s1": r"dswx[\s_-]*s-?1\b|opera[\s\w-]{0,12}\bs-?1\b",
}
BEST = re.compile(r"\b(best|top|highest)[\s-]*(ranked|rank|product)\b", re.I)
INTENT = re.compile(r"\b(use|using|with|instead|switch|based on|map(ped)? (it )?(with|from)|"
                    r"from the|go with|choose|pick)\b", re.I)
INSTEAD_OF = re.compile(r"\binstead of\s+(?:the\s+)?([\w\s-]{2,30})", re.I)


def read(text: str) -> str | None:
    """"best", a product id, or None when the message makes no choice."""
    text = text or ""
    back = re.search(r"\b(back to|revert|return to|default)\b", text, re.I)
    if not INTENT.search(text) and not (back and BEST.search(text)):
        return None
    named = [pid for pid, pat in NAMES.items() if re.search(pat, text, re.I)]
    m = INSTEAD_OF.search(text)
    if m:
        dropped = [pid for pid, pat in NAMES.items() if re.search(pat, m.group(1), re.I)]
        named = [pid for pid in named if pid not in dropped]
    if len(named) == 1:
        return named[0]
    if not named and BEST.search(text):
        return "best"
    return None


def label(pid: str, ranking: dict | None = None) -> str:
    """The product's name, from the ranking when it holds it."""
    for row in (ranking or {}).get("ranking") or []:
        if row.get("id") == pid:
            return row.get("alternative") or pid
    from .workflows._mcdm_data import ALTERNATIVES

    return next((a["name"] for a in ALTERNATIVES if a["id"] == pid), pid)


def _path(store: Any) -> Path | None:
    folder = getattr(store, "dir", None)
    return Path(folder) / FILE if folder else None


def get(store: Any) -> dict[str, Any] | None:
    path = _path(store)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") in NAMES else None


def set_choice(store: Any, pid: str, said: str) -> dict[str, Any]:
    if pid not in NAMES:
        raise ValueError(f"unknown flood product {pid!r}; known: {sorted(NAMES)}")
    path = _path(store)
    data = {"id": pid, "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "said": (said or "")[:200]}
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def clear(store: Any) -> bool:
    path = _path(store)
    if path and path.is_file():
        path.unlink()
        return True
    return False
