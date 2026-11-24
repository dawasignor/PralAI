"""Which reference flood map an AOI is compared against -- hardcoded.

The user's decision (2026-11-18): for the thesis experiments the reference is
fixed, not searched for. When a user asks to compare the flood products with a
reference, `accuracy_metrics` is called with no config and looks the session's
AOI up here. Only an AOI that is one of these experiments has a reference; any
other gets one plain sentence saying none is configured -- a reference is never
guessed or borrowed from a neighbouring AOI.

Each experiment lists its references by name; `primary` is used unless the
user names another.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

# A session AOI counts as the experiment's when the two boxes' intersection is
# at least this share of their UNION -- the same place drawn slightly
# differently, not a neighbour, and not a box that merely contains another
# experiment's (the Kelani basin box contains the old lower-Kelani one; a
# share of the smaller box rated them a tie).
MATCH_SHARE = 0.5

REFERENCES: dict[str, dict[str, Any]] = {
    # The MAIN thesis experiment since 2026-11-18 (second change that day).
    "kelani-basin-2025": {
        "bbox": [79.9450, 6.8800, 80.1550, 7.0000],
        "window": ["2025-11-26", "2025-12-04"],
        "primary": "emsr851-aoi02",
        "configs": {
            "emsr851-aoi02": {
                "path": "experiments/kelani-basin-2025/validation/config.yaml",
                "label": "Copernicus EMS EMSR851 AOI02, Kelani Ganga (PlanetScope 3.9 m, 30 Nov 2025)",
            },
        },
    },
    "gampola-2025": {
        "bbox": [80.5460, 7.1368, 80.6310, 7.2450],
        "window": ["2025-11-26", "2025-12-04"],
        "primary": "emsr851-aoi09",
        "configs": {
            "emsr851-aoi09": {
                "path": "experiments/gampola-2025/validation/config.yaml",
                "label": "Copernicus EMS EMSR851 AOI09, Gampola (GeoEye 0.5 m, 4 Dec 2025)",
            },
            "emsr851-aoi03": {
                "path": "experiments/gampola-2025/validation/config_aoi03.yaml",
                "label": "Copernicus EMS EMSR851 AOI03, Mahaweli Ganga (Legion 1.6 m, 3 Dec 2025)",
            },
        },
    },
    "kelani-lower-2025": {
        "bbox": [79.94, 6.905, 80.09, 6.978],
        "window": ["2025-11-24", "2025-12-01"],
        "primary": "unosat",
        "configs": {
            "unosat": {
                "path": "experiments/kelani-lower-2025/validation/config.yaml",
                "label": "UNOSAT FL20251128LKA (Sentinel-2 merge, 26 Nov - 2 Dec 2025)",
            },
        },
    },
}


class NoReference(LookupError):
    """No reference flood map is configured for this AOI (or by that name)."""


def _overlap_share(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [W, S, E, N] boxes."""
    w, s = max(a[0], b[0]), max(a[1], b[1])
    e, n = min(a[2], b[2]), min(a[3], b[3])
    if e <= w or n <= s:
        return 0.0
    inter = (e - w) * (n - s)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def belongs(config: str, experiment: str) -> bool:
    """Is `config` one of the experiment's own reference configs?"""
    try:
        target = Path(config).resolve()
    except OSError:
        return False
    return any((REPO / c["path"]).resolve() == target
               for c in REFERENCES.get(experiment, {}).get("configs", {}).values())


def same_box(a: list[float], b: list[float]) -> bool:
    """The same AOI, to rounding: an agent passing the session box back is not
    asking for a small test window."""
    return _overlap_share([float(v) for v in a], [float(v) for v in b]) >= 0.98


def experiment_for(bbox: list[float] | None) -> str | None:
    """The experiment whose AOI this box is, or None."""
    if not bbox or len(bbox) != 4:
        return None
    best = max(((_overlap_share([float(v) for v in bbox], spec["bbox"]), name)
                for name, spec in REFERENCES.items()), default=(0.0, None))
    return best[1] if best[0] >= MATCH_SHARE else None


def resolve(bbox: list[float] | None, reference: str = "") -> dict[str, Any]:
    """The reference config for an AOI: {experiment, name, label, path, others}.

    `reference` picks one by name (e.g. "emsr851-aoi03"); empty means the
    experiment's primary. Raises NoReference with a plain reason otherwise.
    """
    experiment = experiment_for(bbox)
    if experiment is None:
        known = ", ".join(sorted(REFERENCES))
        raise NoReference("no reference flood map is configured for this area; references "
                          f"are set up only for the thesis experiments ({known})")
    spec = REFERENCES[experiment]
    name = (reference or spec["primary"]).strip().lower()
    if name not in spec["configs"]:
        raise NoReference(f"{experiment} has no reference called {reference!r}; it has "
                          f"{', '.join(spec['configs'])}")
    entry = spec["configs"][name]
    path = REPO / entry["path"]
    if not path.is_file():
        raise NoReference(f"the {name} reference config is missing ({entry['path']})")
    return {"experiment": experiment, "name": name, "label": entry["label"],
            "path": str(path), "bbox": spec["bbox"],
            "others": [n for n in spec["configs"] if n != name]}
