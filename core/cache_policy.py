"""The cache toggle, and the per-run folder an uncached run writes to.

One policy for the whole run:

* `on` (default): results are read from and written to the named experiment
  and the session folder, as before.
* `off`: every tool re-fetches. A tool taking `use_cache` is called with
  `use_cache=False`, and the experiment cache and the session folder are
  neither read nor written. Everything the run produces goes to
  `runs/<run_id>/` instead (inputs, rasters, facts, errors, metrics).

**An uncached run never writes `experiments/<event>/inputs/`.** Those payloads
are the thesis record, frozen byte-for-byte (core/experiment.py). An uncached
run is a re-measurement, and letting it overwrite the record would mean the
next cached run rests on whichever fetch happened last. `Experiment.cache_input`
refuses under `off` for anything that is not a `RunStore`, so the guarantee is
structural rather than a convention each caller has to remember.

The static archives -- the OSM tile cache and the GLO-30 bbox cache -- are
bypassed too when `off`, so an uncached run's cost is honest. That is logged.

A ContextVar and not a global, for the same reason as the session record:
each console run has its own thread, and one run's policy must never leak into
another's.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from .experiment import Experiment

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"

ON, OFF = "on", "off"

_POLICY: ContextVar[str] = ContextVar("cache_policy", default=ON)
_RUN_ID: ContextVar[str] = ContextVar("run_id", default="")


class CachePolicyError(RuntimeError):
    """A write the cache policy forbids -- an uncached run reaching the record."""


def parse(value: object) -> str:
    """`on` or `off` from a CLI flag, a console field or a config value.

    Anything unrecognised is refused rather than read as a default: a typo that
    quietly turned an uncached RQ5 run into a cached one would make the whole
    batch measure the cache.
    """
    if isinstance(value, bool):
        return ON if value else OFF
    text = str(value).strip().lower()
    if text in ("on", "true", "1", "yes"):
        return ON
    if text in ("off", "false", "0", "no"):
        return OFF
    raise ValueError(f"cache policy must be on or off, got {value!r}")


def new_run_id() -> str:
    """A sortable, unique run id: UTC stamp plus a short random suffix."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def current() -> str:
    return _POLICY.get()


def is_off() -> bool:
    return _POLICY.get() == OFF


def run_id() -> str:
    return _RUN_ID.get()


@contextmanager
def use(policy: str = ON, run: str = ""):
    """Set the policy and the run id for everything inside the block."""
    p_token = _POLICY.set(parse(policy))
    r_token = _RUN_ID.set(run or _RUN_ID.get() or new_run_id())
    try:
        yield _RUN_ID.get()
    finally:
        _POLICY.reset(p_token)
        _RUN_ID.reset(r_token)


class RunStore(Experiment):
    """`runs/<run_id>/`: the experiment cache pointed at one run's folder.

    The same shape as `SessionStore`, so a flow's declared inputs, the fact
    sheet and `read_monty_payload` read an uncached run exactly as they read an
    experiment -- they are simply pointed somewhere else.
    """

    run_scoped = True

    def __init__(self, rid: str, root: Path | None = None, event: str = ""):
        if not rid:
            raise ValueError("a RunStore needs a run id")
        # Run ids carry an uppercase T and Z; Experiment's slug rule is for
        # names a user chooses, so it is checked here on the lowered form and
        # the real id is kept for the folder.
        super().__init__(rid.lower(), root=root or RUNS_DIR)
        # A pipeline run re-measures ONE event: its slug is the event's, so
        # the per-experiment settings and the output headings are that
        # event's. The folder stays the run's.
        if event:
            self.slug = Experiment(event).slug
        self.run_id = rid
        self.dir = (root or RUNS_DIR) / rid
        self.inputs_dir = self.dir / "inputs"
        self.deliverables_dir = self.dir / "deliverables"
        self.rasters_dir = self.dir / "rasters"

    def create(self, place: str = "", window: str = "", note: str = "") -> Path:
        # A place or a window is written to the run's own event.md, so a
        # report rendered from the run reads them from the run.
        if place or window:
            return super().create(place, window, note)
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def _reindex(self) -> None:
        # No event.md: the run's readable record is facts.md and metrics.jsonl.
        return None

    def archive(self, reason: str = "") -> Path | None:
        # A run is one AOI and one window. If the resolver moves either mid-run
        # the inputs go aside, the same as a session folder's.
        from .session_store import SessionStore
        return SessionStore.archive(self, reason)  # type: ignore[arg-type]


def run_dir(rid: str = "") -> Path | None:
    """`runs/<run_id>/` for the current (or given) run, or None without one."""
    rid = rid or run_id()
    return RUNS_DIR / rid if rid else None


def run_store() -> RunStore | None:
    """The current run's store, or None outside a run."""
    rid = run_id()
    return RunStore(rid) if rid else None
