"""The thesis experiment cache: one event, its numbered outputs, inputs kept once.

An experiment run is expensive in a way the rest of this repo is not. Fetching
weather and land cover for an AOI costs real money and real minutes, and the
numbers must not move between the MCDM run, the sensitivity analysis and the
SMAA run -- a result table that cannot be reproduced from the inputs that made
it is not a thesis result. So inputs are fetched **once** and written down, and
every output that rests on them says which inputs it used.

Layout, one directory per event:

    experiments/<event>/
        event.md                  the memory: AOI, window, and what is cached
        inputs/<name>.json        the exact payload a workflow returned
        01_data_availability.md   \
        02_weather.md              |  the outputs, in the order they are
        ...                        |  produced (registry v2, below)
        12_executive_summary.md   /

**`event.md` is the readable half; `inputs/*.json` is the exact half.** A
72-point hourly weather series pasted into markdown is unreadable and, once a
model has reformatted it twice, no longer the same series. So the memory file
carries the AOI, the window and a one-line summary of each cached input, and
the payload it summarises sits beside it byte-for-byte as JSON.

Nothing here expires. A cache that quietly refreshes itself would mean two
tables in the same thesis chapter resting on different numbers, which is the
exact failure this exists to prevent. `purge()` is the only way anything
leaves, and it is never called on your behalf.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "experiments"

# Slugs are used as directory names, so they are constrained rather than
# sanitised: a name that needs rewriting to be safe is a name the user did not
# actually choose, and silently renaming their event is worse than refusing it.
_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Output:
    """One of the eight results, in the order they are produced."""

    n: int
    slot: str
    title: str
    note: str = ""

    @property
    def filename(self) -> str:
        return f"{self.n:02d}_{self.slot}.md"


# Registry version 1: the eight outputs every experiment before 2026-10-25 was
# written to. Kept, never renamed: kelani-2025, kelani-2025-west, bangkok-2026
# and the rest hold files under these names, and an experiment holding any of
# them is read as version 1 (`Experiment.registry_version`).
#
# A fixed registry rather than free-form filenames: a typo in a slot name would
# otherwise create an output that nothing reads and nobody notices missing
# from the thesis. `write()` refuses a name that is not on the experiment's list.
OUTPUTS_V1: tuple[Output, ...] = (
    Output(1, "weather_data", "Weather data",
           "The event's meteorological inputs, from the cached payload."),
    Output(2, "lulc_info", "LULC info",
           "Land-cover indices and class breakdown for the AOI."),
    Output(3, "mcdm_matrix_pre", "MCDM decision matrix -- scores and weights before the run",
           "The matrix as it stands BEFORE ranking: criterion values, the "
           "scores derived from them, and the weights. Written first so the "
           "ranking cannot be read back into the inputs that produced it."),
    Output(4, "mcdm_ranking", "MCDM final ranking and decision matrix",
           "The ranking, and the matrix it came from."),
    Output(5, "sensitivity_analysis", "Sensitivity analysis result",
           "How the ranking moves as weights and scores are perturbed."),
    Output(6, "smaa", "SMAA result",
           "Stochastic multicriteria acceptability analysis: rank "
           "acceptability indices over the weight space."),
    Output(7, "uncertainty_nrt", "Uncertainty in using NRT products for flood mapping",
           "The uncertainty factors themselves -- what is not known about the "
           "candidate products, and what that does to a flood map."),
    Output(8, "road_transport_risk", "Road and transport network flood risk",
           "Potential risk to the road and transport network in the AOI."),
)

# Registry version 2: the Flood
# analyst outputs 1-10, then the two named reports. SMAA is gone (D-27); road
# and transport risk folds into Output 9 (D-34).
OUTPUTS_V2: tuple[Output, ...] = (
    Output(1, "data_availability", "Data availability",
           "Which of the five NRT flood products observed the AOI on each day of "
           "the window. First, before any analysis (D-33)."),
    Output(2, "weather", "Weather data",
           "The event's rain, cloud and wind over the AOI, from fetch_weather."),
    Output(3, "land_use_pre_event", "Land use before the event",
           "Sentinel-2 land cover, or the WorldCover fallback when Sentinel-2 "
           "failed its threshold (D-8), with the remote sensing expert's brief."),
    Output(4, "terrain", "Terrain and slope classes",
           "Elevation and slope classes from the Copernicus DEM."),
    Output(5, "water_and_structures", "Permanent water and structures",
           "The normally-wet baseline and what is in the AOI: places, buildings "
           "of interest and water infrastructure."),
    Output(6, "mcdm_pre_ranking", "MCDM decision matrix before the ranking",
           "The decision matrix before ranking: criteria, scores, weights. Written "
           "first so the ranking cannot be read back into its inputs."),
    Output(7, "mcdm_ranking", "MCDM ranking", "The TOPSIS ranking of the products."),
    Output(8, "sensitivity", "Sensitivity analysis",
           "How far each weight can move before the ranking changes."),
    Output(9, "essential_services_hev", "Essential services (hazard, exposure, vulnerability)",
           "Critical infrastructure, roads and accessibility, food and water, as "
           "hazard, exposure and vulnerability, from the best-ranked flood extent."),
    Output(10, "human_health_risks_hev", "Human health risks (hazard, exposure, vulnerability)",
           "Health risks from the floodwater, as hazard, exposure and vulnerability."),
    Output(11, "public_report", "Public report",
           "The Flood bot report for the public (D-34)."),
    Output(12, "executive_summary", "Executive summary of the event",
           "The Chanakya summary for a mayor (D-34)."),
)

REGISTRIES = {1: OUTPUTS_V1, 2: OUTPUTS_V2}
LATEST = 2
# The current registry. `BY_SLOT` is its lookup; an experiment on version 1
# uses its own (`Experiment.outputs`).
OUTPUTS = OUTPUTS_V2
BY_SLOT = {o.slot: o for o in OUTPUTS}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


class Experiment:
    """One event's cache. Creating it creates the directory."""

    def __init__(self, slug: str, root: Path | None = None):
        slug = (slug or "").strip().lower()
        if not _SAFE.match(slug):
            raise ValueError(
                f"event slug {slug!r} must be lowercase letters, digits, '-' "
                "or '_', starting with a letter or digit")
        self.slug = slug
        self.dir = (root or ROOT) / slug
        self.inputs_dir = self.dir / "inputs"
        self.deliverables_dir = self.dir / "deliverables"

    # -- setting up --------------------------------------------------------
    def create(self, place: str = "", window: str = "", note: str = "") -> Path:
        """Make the directory and write `event.md` if it is not there yet."""
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text(
                _frontmatter({
                    "type": "experiment-memory",
                    "event": self.slug,
                    "place": place,
                    "window": window,
                    "created": _now(),
                    "purged": "never",
                    "registry_version": LATEST,
                })
                + f"\n\n# {self.slug}\n\n"
                + (note + "\n\n" if note else "")
                + "The inputs this event's results rest on. Fetched once and\n"
                  "kept, so every output below is computed from the same\n"
                  "numbers. Payloads are in `inputs/`.\n\n"
                  "## Cached inputs\n\n_None yet._\n",
                encoding="utf-8")
        return self.dir

    @property
    def memory_path(self) -> Path:
        return self.dir / "event.md"

    # -- which output registry -------------------------------------------
    @property
    def registry_version(self) -> int:
        """1 or 2. An explicit `registry_version:` in event.md wins; otherwise
        an experiment that already holds a version-1 output file is version 1
        (its files are never renamed), and anything else is the latest."""
        if self.memory_path.exists():
            for line in self.memory_path.read_text(encoding="utf-8").splitlines()[:12]:
                if line.startswith("registry_version:"):
                    try:
                        return int(line.split(":", 1)[1].strip())
                    except ValueError:
                        break
        v2_files = {x.filename for x in OUTPUTS_V2}
        if any((self.dir / o.filename).exists() for o in OUTPUTS_V1
               if o.filename not in v2_files):
            return 1
        return LATEST

    @property
    def outputs(self) -> tuple[Output, ...]:
        return REGISTRIES[self.registry_version]

    def _slot(self, slot: str) -> Output:
        lookup = {o.slot: o for o in self.outputs}
        if slot not in lookup:
            raise KeyError(f"unknown output {slot!r} for registry version "
                           f"{self.registry_version}; expected one of {sorted(lookup)}")
        return lookup[slot]

    def _check_writable(self, what: str) -> None:
        """Refuse a write to the record during an uncached run.

        An uncached run re-measures; the experiment folder is the frozen
        record. Letting the first overwrite the second would make the next
        cached run rest on whichever fetch happened last. Its results go to
        `runs/<run_id>/` (a `RunStore`) instead -- core/cache_policy.py.
        """
        from . import cache_policy

        if cache_policy.is_off() and not getattr(self, "run_scoped", False):
            raise cache_policy.CachePolicyError(
                f"uncached run {cache_policy.run_id() or '?'} may not write "
                f"{what} to {self.dir}; it writes to runs/<run_id>/")

    def path(self, slot: str) -> Path:
        return self.dir / self._slot(slot).filename

    # -- the eight outputs -------------------------------------------------
    def write(self, slot: str, body: str, *, inputs_used: list[str] | None = None,
              source: str = "", data_source: str = "") -> Path:
        """Write one output, replacing whatever was there.

        `inputs_used` names the cached inputs the result rests on. It is
        recorded rather than inferred: an output whose provenance is a guess is
        not usable in a thesis, and the writer is the only one who knows.
        """
        self._check_writable(f"output {slot!r}")
        target = self.path(slot)
        entry = self._slot(slot)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _frontmatter({
                "type": "experiment-output",
                "event": self.slug,
                "n": entry.n,
                "slot": entry.slot,
                "title": entry.title,
                "data_source": data_source or "unrecorded",
                "registry_version": self.registry_version,
                "written": _now(),
                "source": source or "unrecorded",
                "inputs_used": inputs_used or [],
            })
            + f"\n\n# {self.heading(entry, data_source)}\n\n{body.rstrip()}\n",
            encoding="utf-8")
        return target

    def heading(self, entry: Output, data_source: str = "") -> str:
        """`Module 2: Weather data from ECMWF IFS ...` on registry v2 (the
        user's wording, 2026-10-25); v1 outputs keep `1. Weather data`."""
        if self.registry_version == 1:
            return f"{entry.n}. {entry.title}"
        return f"Module {entry.n}: {entry.title}" + (f" from {data_source}" if data_source else "")

    def read(self, slot: str) -> str:
        target = self.path(slot)
        return target.read_text(encoding="utf-8") if target.exists() else ""

    def status(self) -> list[dict[str, Any]]:
        """Every output slot and whether it has been written."""
        out = []
        for entry in self.outputs:
            target = self.dir / entry.filename
            out.append({
                "n": entry.n,
                "slot": entry.slot,
                "title": entry.title,
                "written": target.exists(),
                "bytes": target.stat().st_size if target.exists() else 0,
            })
        return out

    # -- cached workflow inputs -------------------------------------------
    def cache_input(self, name: str, payload: Any, *, summary: str = "",
                    params: dict[str, Any] | None = None) -> Path:
        """Store a workflow's result under `name`, so it is not fetched twice.

        `name` is chosen by the caller -- "weather", "lulc", "place_context" --
        rather than derived from the parameters. The point of this cache is
        that the thesis uses ONE weather series, not one per parameter
        combination that happened to be tried; a hash-keyed cache would let two
        chapters quietly rest on different fetches.

        The parameters are recorded alongside so the fetch can be repeated.
        """
        if not _SAFE.match(name):
            raise ValueError(f"input name {name!r} must be a simple slug")
        self._check_writable(f"input {name!r}")
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        stale = self.failures_dir / f"{name}.json"
        if stale.exists():
            stale.unlink()                 # the latest outcome is a success
        target = self.inputs_dir / f"{name}.json"
        target.write_text(json.dumps({
            "name": name,
            "cached": _now(),
            "params": params or {},
            "summary": summary,
            "payload": payload,
        }, indent=2, default=str), encoding="utf-8")
        self._reindex()
        return target

    # -- recorded failures --------------------------------------------------
    # The latest failure of each tool, beside the inputs (`inputs/_failures/`),
    # so it moves aside with them when the AOI or window changes. A failure
    # is evidence too: the land-cover fallback may use WorldCover only when
    # Sentinel-2 is on record as having failed its threshold for this AOI.
    # A later success of the same tool clears it.
    @property
    def failures_dir(self) -> Path:
        return self.inputs_dir / "_failures"

    def record_failure(self, name: str, error: dict[str, Any], *,
                       params: dict[str, Any] | None = None) -> Path:
        if not _SAFE.match(name):
            raise ValueError(f"failure name {name!r} must be a simple slug")
        self._check_writable(f"failure {name!r}")
        self.failures_dir.mkdir(parents=True, exist_ok=True)
        target = self.failures_dir / f"{name}.json"
        record = {k: v for k, v in error.items() if k != "traceback"}
        target.write_text(json.dumps({
            "name": name, "failed": _now(), "params": params or {},
            "error": record}, indent=2, default=str), encoding="utf-8")
        return target

    def failure(self, name: str) -> dict[str, Any] | None:
        target = self.failures_dir / f"{name}.json"
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def cached_input(self, name: str) -> dict[str, Any] | None:
        """The stored record, or None. `record["payload"]` is the result."""
        target = self.inputs_dir / f"{name}.json"
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A half-written file is a miss, not a crash: the caller refetches
            # and overwrites it, which is the right repair.
            return None

    def inputs(self) -> list[dict[str, Any]]:
        if not self.inputs_dir.exists():
            return []
        out = []
        for path in sorted(self.inputs_dir.glob("*.json")):
            record = self.cached_input(path.stem) or {}
            out.append({
                "name": path.stem,
                "cached": record.get("cached", "?"),
                "summary": record.get("summary", ""),
                "bytes": path.stat().st_size,
            })
        return out

    def _reindex(self) -> None:
        """Rewrite the `## Cached inputs` table in `event.md`."""
        if not self.memory_path.exists():
            self.create()
        text = self.memory_path.read_text(encoding="utf-8")
        rows = self.inputs()
        if rows:
            table = ["| Input | Cached | Size | Summary |", "|---|---|---|---|"]
            table += [f"| `{r['name']}` | {r['cached']} | {r['bytes']:,} B | "
                      f"{r['summary'] or '--'} |" for r in rows]
            block = "\n".join(table)
        else:
            block = "_None yet._"

        marker = "## Cached inputs"
        head, sep, _ = text.partition(marker)
        if not sep:                       # someone removed the heading
            text = text.rstrip() + f"\n\n{marker}\n\n"
            head, sep = text, marker
        self.memory_path.write_text(
            f"{head}{marker}\n\n{block}\n", encoding="utf-8")

    # -- deliverables ------------------------------------------------------
    def write_deliverable(self, name: str, text: str, *, agent: str = "",
                          flow: str = "", brief: str = "") -> Path:
        """Store a specialist's own words, verbatim.

        Separate from `inputs/` on purpose. An input is something an agent is
        fed; a deliverable is something a reader is shown. Keeping a written
        brief out of `inputs/` is what stops a later flow consuming an earlier
        flow's prose as though it were measured data -- a summary of a summary
        reaching a thesis as a finding.

        Nothing here reads deliverables back into a prompt, and nothing should.
        """
        if not _SAFE.match((name or "").strip().lower()):
            raise ValueError(f"deliverable name {name!r} must be a simple slug")
        self._check_writable(f"deliverable {name!r}")
        target = self.deliverables_dir / f"{name.strip().lower()}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "name": name,
            "agent": agent,
            "flow": flow,
            "written": _now(),
            "brief": brief,
            "text": text,
        }, indent=2, default=str), encoding="utf-8")
        return target

    def deliverable(self, name: str) -> dict[str, Any] | None:
        target = self.deliverables_dir / f"{name.strip().lower()}.json"
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def deliverables(self) -> list[str]:
        folder = self.deliverables_dir
        if not folder.exists():
            return []
        return sorted(p.stem for p in folder.glob("*.json"))

    # -- removing things ---------------------------------------------------
    def purge(self, *, outputs: bool = True, inputs: bool = False,
              slots: list[str] | None = None, names: list[str] | None = None,
              deliverables: list[str] | None = None) -> list[str]:
        """Delete cached files. Never called automatically.

        Defaults to every output and no input. Inputs are what make two results
        comparable, and throwing them away silently would mean the next run
        rests on a different fetch of the same thing -- so dropping them has to
        be asked for explicitly.

        **`slots`, `names` and `deliverables` narrow it to one flow.** Re-running
        the LULC flow should not cost the weather output that was fine, and the
        all-or-nothing version made "purge the results of this flow" into a
        choice between deleting too much and deleting by hand. Each list
        restricts its own category; `None` means the whole category, subject to
        the two booleans.

        A name that matches nothing is an error, not a no-op: the whole point of
        naming what to delete is that you believed it was there, and a silent
        miss leaves stale output behind under the impression it is gone.
        """
        removed: list[str] = []

        if slots is not None:
            known = {o.slot for o in self.outputs}
            unknown = [s for s in slots if s not in known]
            if unknown:
                raise ValueError(
                    f"no such output slot(s): {unknown}. "
                    f"Known slots: {sorted(known)}")

        if outputs:
            for entry in self.outputs:
                if slots is not None and entry.slot not in slots:
                    continue
                target = self.dir / entry.filename
                if target.exists():
                    target.unlink()
                    removed.append(entry.filename)

        if inputs and self.inputs_dir.exists():
            wanted = None if names is None else set(names)
            if wanted is not None:
                missing = sorted(
                    n for n in wanted
                    if not (self.inputs_dir / f"{n}.json").exists())
                if missing:
                    raise ValueError(
                        f"no cached input(s) named {missing}; nothing was "
                        f"deleted. Present: "
                        f"{sorted(q.stem for q in self.inputs_dir.glob('*.json'))}")
            for path in sorted(self.inputs_dir.glob("*.json")):
                if wanted is not None and path.stem not in wanted:
                    continue
                path.unlink()
                removed.append(f"inputs/{path.name}")
            self._reindex()

        if deliverables is not None and self.deliverables_dir.exists():
            for name in deliverables:
                for path in sorted(self.deliverables_dir.glob(f"{name}*.json")):
                    path.unlink()
                    removed.append(f"deliverables/{path.name}")

        return removed


# The experiment this run belongs to, published for the duration of the run.
#
# Same shape as `session._CURRENT` and for the same reason: tools receive no
# state, so the only way `read_monty_payload` can know which event it is
# reading is a ContextVar set around the graph invocation. A ContextVar and not
# a global -- each run has its own thread, and one run must never read
# another's cache.
_CURRENT: ContextVar["Experiment | None"] = ContextVar("experiment", default=None)


def set_current(run: "Experiment | None") -> None:
    _CURRENT.set(run)


def current() -> "Experiment | None":
    return _CURRENT.get()


# Keys that carry a raster or a file path rather than a statistic.
#
# Norwood's contract is "summary statistics, class fractions and histograms
# only. No raster access." Stripping these makes that structural instead of an
# instruction the model may or may not follow -- it cannot cite a raster it was
# never shown. The paths still exist in the cached payload; they are simply not
# what this tool serves.
_RASTER_KEYS = {"raster", "rasters", "path", "png", "source_tif", "tif"}


def stats_only(value: Any) -> Any:
    """A payload with every raster path and file reference removed."""
    if isinstance(value, dict):
        return {k: stats_only(v) for k, v in value.items()
                if k not in _RASTER_KEYS}
    if isinstance(value, list):
        return [stats_only(v) for v in value]
    return value


def events(root: Path | None = None) -> list[str]:
    """Every event directory that exists."""
    base = root or ROOT
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and (p / "event.md").exists())
