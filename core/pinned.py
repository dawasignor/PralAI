"""The one session the console works in, and its hardcoded context.

User decision 2026-11-21: the experiment results for the Kelani basin are
built in ONE session, module by module, and the app refers to that session
only -- across reloads and server restarts. Its AOI and window are not read
from a message or a gazetteer: they are these constants, applied every time
the record is loaded (`session.load`), so nothing a turn says can move them.

    session   kelani-basin-2025
    AOI       Kelani river basin, Sri Lanka, bbox W 79.9450 S 6.8800 E 80.1550 N 7.0000
    window    26 Nov - 4 Dec 2025 (local days)

The values are the main experiment's (reference Copernicus EMS
EMSR851 AOI02). Changing the experiment means changing them here, and
nowhere else.
"""

from __future__ import annotations

from typing import Any

SESSION_ID = "kelani-basin-2025"

PLACE: dict[str, Any] = {
    "name": "Kelani river basin, Sri Lanka",
    "display": "Kelani river basin, Sri Lanka",
    "bbox": (79.9450, 6.8800, 80.1550, 7.0000),
    "country": "Sri Lanka",
    "admin": "Western Province",
    "source": "hardcoded: main experiment kelani-basin-2025",
    "retrieved": "2026-11-18",
}

WINDOW: dict[str, Any] = {
    "start": "2025-11-26",
    "end": "2025-12-04",
    "event": "Kelani river basin flood, Sri Lanka",
    "glide": "",
    "source": "hardcoded: main experiment kelani-basin-2025",
    "retrieved": "2026-11-18",
}


def is_pinned(session_or_id: Any) -> bool:
    """True for the pinned session, given its id or its record."""
    sid = session_or_id if isinstance(session_or_id, str) \
        else getattr(session_or_id, "session_id", "")
    return bool(sid) and sid == SESSION_ID


def apply(record: Any) -> bool:
    """Set the hardcoded AOI and window on the pinned session's record.

    Returns True when anything had to change. A record for any other session
    is left alone.
    """
    if not is_pinned(record):
        return False
    from .session import Place, Window

    place, window = Place(**PLACE), Window(**WINDOW)
    changed = (record.place != place or record.window != window
               or not record.confirmed)
    record.place, record.window, record.confirmed = place, window, True
    # A switch question can never be pending here: nothing may move the AOI.
    if (record.pending or {}).get("switch"):
        record.pending = {}
        changed = True
    return changed


def refusal() -> str:
    """The answer to a message that would change the area or the dates."""
    bbox = ", ".join(f"{v:.4f}" for v in PLACE["bbox"])
    return ("This console works on one fixed area and time frame:\n\n"
            f"- Area: **{PLACE['display']}** (bbox W, S, E, N: {bbox})\n"
            f"- Time frame: **26 Nov - 4 Dec 2025**\n\n"
            "They cannot be changed here. Ask about this area and these dates, "
            "and every result is built on them.")
