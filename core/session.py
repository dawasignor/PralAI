"""The facts that stay true for a whole session, and the store that keeps them.

A run used to begin and end inside one turn: `new_state()` built an empty
GraphState, the graph filled it, the answer went back, and everything else was
discarded. The AOI, the time window and what the specialists had already
established did not survive to the next message, so a follow-up question was
answered by an orchestrator that had to re-derive them or invent them.

This module is the memory that was missing. One `SessionRecord` per
`sessionId`, holding two different kinds of thing:

  * **Constants** -- the AOI, the event window, the demographics. Resolved
    once, confirmed with the user, and then never re-derived. Every agent must
    agree on these, so getting them wrong is the failure this module exists to
    prevent.
  * **A digest** -- a short account of what the agents have delivered so far,
    rewritten each turn by Argos.

Access is split by lifetime, the same way a flow card is split from its
knowledge note. `header()` is small enough to inject into every prompt
unconditionally; `detail()` is everything, reached through the `read_session`
tool by whoever actually needs it. The constants an agent must never get wrong
are therefore never merely *available* -- they are already in front of it.

The whole record is held under TOKEN_BUDGET. Only the digest grows, so the
digest is what gets trimmed.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

SESSIONS_DIR = REPO_ROOT / "sessions"

# The ceiling the whole record is kept under. Not a hard API limit -- a budget,
# because this text is prepended to work that is already paying for a flow card
# and a knowledge note.
TOKEN_BUDGET = 1000

# The header is injected into every prompt of every agent on every turn, so it
# is costed separately and much harder.
HEADER_BUDGET = 200


def approx_tokens(text: str) -> int:
    """Rough token count: four characters to a token.

    Deliberately an estimate. An exact count needs the tokenizer of whichever
    model the slot happens to point at, and a budget that changes when .env
    changes is worse than one that is approximate and stable.
    """
    return (len(text) + 3) // 4


def _now() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass
class Place:
    """An AOI, resolved to coordinates and named the way a gazetteer names it.

    `bbox` is (west, south, east, north) in EPSG:4326 -- the order the
    workflows already take, so a bbox from here can be handed to
    `fetch_lulc_data` without being rearranged on the way.
    """

    name: str = ""              # what the user typed
    display: str = ""           # the gazetteer's full name, disambiguated
    bbox: tuple[float, float, float, float] | None = None
    country: str = ""
    admin: str = ""             # state / province, when the gazetteer gives one
    source: str = ""
    retrieved: str = ""

    def line(self) -> str:
        if not self.display:
            return ""
        box = (", ".join(f"{v:.4f}" for v in self.bbox)
               if self.bbox else "no bounding box")
        return f"{self.display} | bbox(W,S,E,N) {box}"


@dataclass
class Window:
    """A time frame, anchored to a named event wherever one exists.

    An event reference is what makes a window defensible: "November 2021" is a
    guess about what the user meant, and "Andhra Pradesh floods, 18-21 Nov
    2021 (GLIDE FL-2021-000175-IND)" is a claim someone can check.
    """

    start: str = ""             # ISO date
    end: str = ""
    event: str = ""             # the event the window is anchored to
    glide: str = ""             # GLIDE number, when the record carries one
    source: str = ""
    retrieved: str = ""

    def line(self) -> str:
        if not (self.start or self.event):
            return ""
        span = f"{self.start} to {self.end}" if self.end else self.start
        event = f" | {self.event}" if self.event else ""
        glide = f" [{self.glide}]" if self.glide else ""
        return f"{span}{event}{glide}"


@dataclass
class Demographics:
    """Population figures for the AOI, with the vintage attached.

    The vintage travels with the number for the same reason a citation does: a
    2011 census figure used in 2026 is not wrong, but presenting it without its
    year is.
    """

    population: int | None = None
    density_per_km2: float | None = None
    source: str = ""
    vintage: str = ""

    def line(self) -> str:
        if self.population is None and self.density_per_km2 is None:
            return ""
        parts = []
        if self.population is not None:
            parts.append(f"population {self.population:,}")
        if self.density_per_km2 is not None:
            parts.append(f"{self.density_per_km2:,.0f}/km2")
        vintage = f" ({self.source} {self.vintage})".rstrip() if self.source else ""
        return ", ".join(parts) + vintage


@dataclass
class SessionRecord:
    """Everything that stays true across the turns of one session."""

    session_id: str
    place: Place = field(default_factory=Place)
    window: Window = field(default_factory=Window)
    demographics: Demographics = field(default_factory=Demographics)

    # Run settings. Re-sent by the console every turn, so the record follows
    # the console rather than the other way round.
    persona: str = ""
    verified_sources_only: bool = False

    # Has the user agreed to the AOI and window, or are they still a guess?
    # An unconfirmed record is usable -- it is just never presented as settled.
    confirmed: bool = False
    # Candidates offered to the user and awaiting an answer. Cleared as soon as
    # one is chosen, so a stale question cannot be answered two turns later.
    pending: dict[str, Any] = field(default_factory=dict)

    turns: int = 0
    flows_run: list[str] = field(default_factory=list)
    # Monty workflows that succeeded in this session, for this AOI and window.
    # What lets Argus tell "the land cover is already fetched, comment on it"
    # from "fetch it first" -- and suggest the prerequisite instead of
    # answering a raster question from nothing. Cleared when the AOI or the
    # window changes: data for the old place is not data for the new one.
    workflows_run: list[str] = field(default_factory=list)
    digest: str = ""            # what the agents have delivered so far
    # The flood-map product the user asked Modules 9-10 to map (product id),
    # "" = the best-ranked (core/product_choice.py, 2026-11-18). Cleared with
    # the AOI or window.
    flood_product: str = ""

    # -- views -------------------------------------------------------------
    def header(self) -> str:
        """The constants, small enough to sit in every prompt.

        Only what an agent would get *wrong* without it. Demographics and the
        digest are left out on purpose: an agent that needs them can ask, and
        an agent that does not should not pay for them on every turn.

        **A constant that is not set says so.** The resolver no longer stops a
        turn to ask for a place or dates (since 2026-10-10), so a session can
        reach an agent without them -- and an agent told nothing does not ask,
        it invents an AOI, and the answer reads like any other. "Not set" is
        the instruction not to.
        """
        place, window = self.place.line(), self.window.line()
        lines = [
            f"- AOI: {place}" if place else
            "- AOI: NOT SET. Do not assume one. If the answer needs an area, "
            "say so and ask for it.",
            f"- Time frame: {window}" if window else
            "- Time frame: NOT SET. Do not assume one. If the answer needs "
            "dates, say so and ask for them.",
        ]

        if (place or window) and not self.confirmed:
            lines.append("- Status: NOT yet confirmed by the user -- say so if "
                         "you rely on it.")
        if self.flows_run:
            lines.append(f"- Already run: {', '.join(self.flows_run[-6:])}")
        if self.flood_product:
            from .product_choice import label as _product_label

            lines.append(f"- Flood map product: {_product_label(self.flood_product)} -- the "
                         "user's choice; map and describe THIS product, not the best-ranked.")
        lines.append("- Data fetched for this AOI and window: "
                     + (", ".join(self.workflows_run[-6:]) or "none yet"))
        lines.append("- These are the session's constants. Use them as given; "
                     "do not re-derive or substitute an AOI or window of your "
                     "own. Call `read_session` for demographics and for what "
                     "has been established so far.")
        return "### Session\n" + "\n".join(lines)

    def detail(self) -> str:
        """The whole record, as the `read_session` tool returns it."""
        rows = [
            ("Session", self.session_id),
            ("AOI", self.place.line() or "not resolved"),
            ("AOI source", f"{self.place.source} {self.place.retrieved}".strip()
             or "n/a"),
            ("Time frame", self.window.line() or "not resolved"),
            ("Time frame source",
             f"{self.window.source} {self.window.retrieved}".strip() or "n/a"),
            ("Demographics", self.demographics.line() or "not available"),
            ("Confirmed with user", "yes" if self.confirmed else "no"),
            ("Persona", self.persona or "default"),
            ("Verified sources only", "on" if self.verified_sources_only else "off"),
            ("Turns so far", str(self.turns)),
            ("Analyses run", ", ".join(self.flows_run) or "none"),
            ("Data fetched", ", ".join(self.workflows_run) or "none"),
        ]
        body = "\n".join(f"- **{key}:** {value}" for key, value in rows)
        digest = self.digest or "Nothing delivered yet -- this is the first turn."
        return (f"## Session record\n{body}\n\n"
                f"## Delivered so far\n{digest}\n")

    # -- budget ------------------------------------------------------------
    def trim(self) -> None:
        """Bring the record back under TOKEN_BUDGET.

        The constants are fixed-size and the digest is the only field that
        grows, so the digest is the only field that can be cut. Trimming is
        marked rather than silent: a digest that stops mid-account would
        otherwise read as an account that simply ended there.
        """
        over = approx_tokens(self.detail()) - TOKEN_BUDGET
        if over <= 0:
            return
        keep = max(0, len(self.digest) - (over * 4) - 40)
        self.digest = self.digest[:keep].rstrip() + "\n\n[digest trimmed to fit]"

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.place.bbox:
            data["place"]["bbox"] = list(self.place.bbox)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        place = Place(**(data.get("place") or {}))
        if place.bbox:
            place.bbox = tuple(place.bbox)       # JSON has no tuples
        return cls(
            session_id=data.get("session_id", ""),
            place=place,
            window=Window(**(data.get("window") or {})),
            demographics=Demographics(**(data.get("demographics") or {})),
            persona=data.get("persona", ""),
            verified_sources_only=bool(data.get("verified_sources_only")),
            confirmed=bool(data.get("confirmed")),
            pending=data.get("pending") or {},
            turns=int(data.get("turns", 0)),
            flows_run=list(data.get("flows_run") or []),
            workflows_run=list(data.get("workflows_run") or []),
            digest=data.get("digest", ""),
            flood_product=data.get("flood_product", ""),
        )


# One FOLDER per session (since 2026-10-15), not one file:
#
#     sessions/<id>/
#         session.json      this record
#         facts.md          the fact sheet the agents read (core/fact_sheet.py)
#         inputs/<name>.json  every workflow result, exactly as returned
#         rasters/          the GeoTIFFs and charts the workflows wrote
#         deliverables/     the specialists' own words
#
# The folder is what lets a later turn stand on an earlier turn's data. Before
# it, a fetch lived only as long as its turn: Pathum Thani fetched weather,
# terrain and water on three turns, and the ranking turn found none of them.
# JSON and markdown on disk, because a human debugging a session wants to open
# the thing in an editor.
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def folder_name(session_id: str) -> str:
    """The session's folder name: lowercase, safe, starting alphanumeric.

    A session id arrives over HTTP. Flattening it keeps it from naming a path
    of its own choosing, and lowercase keeps it a valid experiment-store slug.
    """
    name = _SAFE_ID.sub("_", session_id or "").lower().lstrip("_-")[:64]
    return name or "default"


def folder(session_id: str) -> Path:
    return SESSIONS_DIR / folder_name(session_id)


def _path(session_id: str) -> Path:
    return folder(session_id) / "session.json"


def _legacy_path(session_id: str) -> Path:
    """Where a record lived before sessions had folders."""
    return SESSIONS_DIR / f"{_SAFE_ID.sub('_', session_id)[:64] or 'default'}.json"


def load(session_id: str) -> SessionRecord:
    """The record for this session, or a fresh one.

    A corrupt file yields a fresh record rather than an exception: losing the
    session's memory degrades the next answer, and refusing to run loses it too
    *and* breaks the console. A record from before the folders is still read;
    the next save writes it into the folder.
    """
    from . import pinned

    record = SessionRecord(session_id=session_id)
    for path in (_path(session_id), _legacy_path(session_id)):
        if path.exists():
            try:
                record = SessionRecord.from_dict(
                    json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            break
    # The pinned session's AOI and window are hardcoded (core/pinned.py,
    # user decision 2026-11-21): applied on every load, so the persisted
    # context is always the experiment's, whatever a file or a turn says.
    pinned.apply(record)
    return record


def save(record: SessionRecord) -> None:
    record.trim()
    path = _path(record.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8")


# The record for the run happening right now.
#
# A tool is a plain function with no access to graph state, so `read_session`
# has no other way to reach the record its caller is inside. A ContextVar and
# not a module global: the value belongs to the run, not to the process, and a
# second run must never be able to read the first one's AOI.
_CURRENT: ContextVar[SessionRecord | None] = ContextVar("session_record",
                                                        default=None)


def set_current(record: SessionRecord | None) -> None:
    _CURRENT.set(record)


def current() -> SessionRecord | None:
    return _CURRENT.get()


def touched(record: SessionRecord) -> str:
    """One line for the log: what the session currently believes."""
    return (f"aoi={record.place.display or 'unset'} "
            f"window={record.window.line() or 'unset'} "
            f"confirmed={record.confirmed}")
