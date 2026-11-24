"""Reading `config.yaml` for the validation (plan rule 4: no hard-coded paths).

Every path, threshold, window and encoding comes from the config. A required
value that is missing, or still a placeholder, stops the run with the list of
what is missing -- the plan says do not invent values, so this never fills one
in. Relative paths are resolved against the config file's own folder, so the
config can travel with its experiment.

The shape is documented, with every key explained, in
`docs/accuracy_metrics.config.example.yaml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER = "TODO"
REQUIRED = [
    ("reference", "zip"), ("reference", "flood_layer"), ("reference", "code"),
    ("temporal", "mode"), ("grid", "crs"), ("grid", "resolution_m"),
    ("aoi", "bbox"), ("evaluation", "flood_fraction_threshold"),
    ("evaluation", "domain_fraction_threshold"), ("output_dir",), ("seed",),
]


def _get(cfg: dict, keys: tuple[str, ...]) -> Any:
    value: Any = cfg
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def load(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no config at {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["_dir"] = str(path.parent)
    cfg["_path"] = str(path)

    missing = [".".join(k) for k in REQUIRED
               if _get(cfg, k) in (None, "", PLACEHOLDER)]
    if missing:
        raise ValueError(f"{path.name}: required values missing or still "
                         f"placeholders: {', '.join(missing)}. Fill them in; "
                         "nothing is assumed")

    mode = cfg["temporal"]["mode"]
    if mode not in ("single_date", "cumulative"):
        raise ValueError(f"temporal.mode must be single_date or cumulative, not {mode!r}")
    if mode == "cumulative" and not cfg["temporal"].get("window"):
        raise ValueError("temporal.mode is cumulative but temporal.window is not set")
    if mode == "single_date" and not cfg["temporal"].get("date"):
        raise ValueError("temporal.mode is single_date but temporal.date is not set")
    bbox = cfg["aoi"]["bbox"]
    if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise ValueError(f"aoi.bbox must be [west, south, east, north], got {bbox}")
    for key in ("flood_fraction_threshold", "domain_fraction_threshold"):
        value = cfg["evaluation"][key]
        if not 0.0 <= float(value) < 1.0:
            raise ValueError(f"evaluation.{key} must be in [0, 1), got {value}")
    if not isinstance(cfg.get("products"), dict) or not cfg["products"]:
        raise ValueError("products: no products configured")
    return cfg


def resolve(cfg: dict[str, Any], value: str | None) -> Path | None:
    """A config path, absolute or relative to the config's folder."""
    if not value:
        return None
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (Path(cfg["_dir"]) / candidate).resolve()
