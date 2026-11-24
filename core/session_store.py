"""The session folder's data half: workflow results and rasters, kept on disk.

A session's AOI and window lived in `sessions/<id>.json`, but the data fetched
for them did not live anywhere: a workflow's result reached Argus once, in the
turn that fetched it, and was gone by the next. A session that fetched weather,
terrain and water on three turns asked for a ranking on the fourth and was told
the five indices were "not present in the session" -- they had been computed
from data nobody kept.

So every session now writes its results where a later turn can read them:

    sessions/<id>/inputs/<name>.json    exactly what the workflow returned
    sessions/<id>/rasters/              where the workflows write their files
    sessions/<id>/deliverables/         the specialists' own words

`SessionStore` is the experiment cache (`core/experiment.py`) pointed at that
folder, so everything that already reads an experiment -- a flow's declared
inputs, `read_monty_payload`, deliverables -- reads a session the same way.
It writes no `event.md`: the session's readable half is `facts.md`
(core/fact_sheet.py).

**Data for one AOI is not data for another.** When the AOI or the window
changes, `archive` moves the inputs aside -- never deletes them -- so the fact
sheet cannot describe the old place under the new one's name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import session as _session
from .experiment import Experiment


class SessionStore(Experiment):
    """An experiment cache rooted at one session's folder."""

    def __init__(self, session_id: str):
        super().__init__(_session.folder_name(session_id),
                         root=_session.SESSIONS_DIR)
        self.session_id = session_id
        self.rasters_dir = self.dir / "rasters"

    def create(self, place: str = "", window: str = "", note: str = "") -> Path:
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def _reindex(self) -> None:
        # The experiment rewrites a table in event.md here. A session's
        # readable record is facts.md, rebuilt by core/fact_sheet.py.
        return None

    def archive(self, reason: str = "") -> Path | None:
        """Move the inputs aside, to `previous/<UTC stamp>/`. Nothing is deleted."""
        if not self.inputs_dir.exists() or not any(self.inputs_dir.iterdir()):
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.dir / "previous" / stamp
        target.parent.mkdir(parents=True, exist_ok=True)
        self.inputs_dir.rename(target)
        # A product chosen for the old AOI is not chosen for the new one.
        choice = self.dir / "flood_product.json"
        if choice.is_file():
            choice.rename(target / "flood_product.json")
        if reason:
            (target / "REASON.txt").write_text(reason + "\n", encoding="utf-8")
        return target


def for_record(record: Any) -> SessionStore | None:
    """The store for a session record, or None without one.

    During an uncached run this is the run's own folder, `runs/<run_id>/`
    (core/cache_policy.py), with or without a record: the session folder holds
    results earlier turns fetched, and an uncached run must neither read them
    nor add to them. One choke point, so the fact sheet, the flow inputs, the
    error reports and Monty's keepers all follow the policy together.
    """
    from . import cache_policy

    if cache_policy.is_off():
        return cache_policy.run_store()  # type: ignore[return-value]
    if record is None or not getattr(record, "session_id", ""):
        return None
    try:
        return SessionStore(record.session_id)
    except ValueError:
        return None
