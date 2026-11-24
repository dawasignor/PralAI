"""The labelled sample's frame: strata, allocation, a seeded draw, and a freeze.

Design and decisions: docs/sample_reference.md. The frame is built on the same
grid, reference and domain as `accuracy_metrics`, so the two references are
compared over the same ground.

    S1  any evaluated product says flood
    S2  the reference says flood, no product does
    S3  dry in every map, but JRC ever-wet or within `buffer_m` of S1/S2
    S4  everything else in the domain

S2-S4 are split by "observed by at least one evaluated product" (`.obs` /
`.unobs`), so a product with low coverage still gets usable points. S1 is
observed by definition. The split strata are the design strata: weights are
their areas over their point counts.

**A frozen sample is never redrawn.** The manifest is written last; if it
exists, `build` refuses. A different sample needs a new `kind` or seed, and
the old one stays on disk -- a redraw after seeing labels is how a sample
stops being a probability sample.

**Blindness.** `sample_points.csv` carries the stratum (the estimators need
it) but the labelling server never sends it. Product and reference values at
the points go to `product_values.csv`, which nothing in the labelling path
reads.

Run:  python -m core.workflows.validation.sample <config.yaml> pilot|main
"""

from __future__ import annotations

import csv
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .metrics import FLOOD, NODATA, NONFLOOD

STRATA = ("S1", "S2", "S3", "S4")
# Cell codes in strata.tif: 0 outside the domain.
CODES = {"S1": 1, "S2.obs": 2, "S2.unobs": 3, "S3.obs": 4, "S3.unobs": 5,
         "S4.obs": 6, "S4.unobs": 7}


# -- pure parts (tested in tests/test_sample_frame.py) ----------------------
def disk_dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    """`mask` grown by a disk of `radius_px` cells -- exact on the grid.

    Row by row: a disk is, for each vertical offset dy, a horizontal run of
    half-width floor(sqrt(r^2 - dy^2)). Each run is a window sum on a cumulative
    sum, computed once per distinct half-width. numpy only (no scipy in the
    runtime dependencies).
    """
    mask = np.asarray(mask, dtype=bool)
    if radius_px <= 0 or not mask.any():
        return mask.copy()
    h, w = mask.shape
    cs = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(mask, axis=1, out=cs[:, 1:])
    cols = np.arange(w)
    runs: dict[int, np.ndarray] = {}
    out = np.zeros_like(mask)
    for dy in range(-radius_px, radius_px + 1):
        hw = int(math.floor(math.sqrt(radius_px * radius_px - dy * dy)))
        if hw not in runs:
            lo = np.clip(cols - hw, 0, w)
            hi = np.clip(cols + hw + 1, 0, w)
            runs[hw] = (cs[:, hi] - cs[:, lo]) > 0
        hit = runs[hw]
        if dy >= 0:
            out[:h - dy] |= hit[dy:]
        else:
            out[-dy:] |= hit[:h + dy]
    return out


def stratify(domain: np.ndarray, reference: np.ndarray,
             products: dict[str, np.ndarray], ever_wet: np.ndarray,
             buffer_px: int) -> np.ndarray:
    """Cell codes (CODES) on the evaluation grid; 0 outside the domain.

    `reference` is 1/0/255; each product array is 1/0/255 on the same grid.
    """
    shape = domain.shape
    any_flood = np.zeros(shape, dtype=bool)
    observed = np.zeros(shape, dtype=bool)
    for arr in products.values():
        any_flood |= arr == FLOOD
        observed |= arr != NODATA
    s1 = domain & any_flood
    s2 = domain & ~any_flood & (reference == FLOOD)
    near = disk_dilate(s1 | s2, buffer_px)
    s3 = domain & ~s1 & ~s2 & (ever_wet | near)
    s4 = domain & ~s1 & ~s2 & ~s3

    code = np.zeros(shape, dtype="uint8")
    code[s1] = CODES["S1"]
    for name, s in (("S2", s2), ("S3", s3), ("S4", s4)):
        code[s & observed] = CODES[f"{name}.obs"]
        code[s & ~observed] = CODES[f"{name}.unobs"]
    return code


def allocate(counts: dict[str, int], allocation: dict[str, int],
             observed_share: float) -> tuple[dict[str, int], list[str]]:
    """Points per design stratum, and notes on every shortfall or move.

    S1 takes its whole allocation. S2-S4 split theirs by `observed_share`;
    a part with no cells hands its points to its sibling, and a part with too
    few cells takes all of them -- every move is written into the notes, so
    the manifest says where the plan and the draw differ.
    """
    out: dict[str, int] = {}
    notes: list[str] = []
    for stratum in STRATA:
        n = int(allocation.get(stratum, 0))
        if stratum == "S1":
            parts = {"S1": n}
        else:
            n_obs = int(round(n * observed_share))
            parts = {f"{stratum}.obs": n_obs, f"{stratum}.unobs": n - n_obs}
        keys = list(parts)
        for key in keys:
            have = counts.get(key, 0)
            if parts[key] > have:
                spill = parts[key] - have
                parts[key] = have
                sibling = [k for k in keys if k != key]
                if sibling:
                    room = counts.get(sibling[0], 0) - parts[sibling[0]]
                    moved = max(0, min(spill, room))
                    parts[sibling[0]] += moved
                    spill -= moved
                    if moved:
                        notes.append(f"{key}: {moved} point(s) moved to {sibling[0]} "
                                     f"({have} cell(s) available)")
                if spill:
                    notes.append(f"{stratum}: {spill} point(s) short of the allocation "
                                 f"of {n}; every available cell is taken")
        out.update(parts)
    return out, notes


def draw(code: np.ndarray, per_stratum: dict[str, int],
         rng: np.random.Generator) -> list[tuple[str, int]]:
    """(design stratum, flat cell index) pairs, without replacement."""
    picks: list[tuple[str, int]] = []
    flat = code.ravel()
    for key in CODES:
        n = per_stratum.get(key, 0)
        if n <= 0:
            continue
        cells = np.flatnonzero(flat == CODES[key])
        chosen = rng.choice(cells, size=n, replace=False)
        picks.extend((key, int(c)) for c in np.sort(chosen))
    return picks


# -- I/O ------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _on_grid(comp: dict[str, Any], grid: dict[str, Any]) -> np.ndarray:
    """A product composite on the 10 m evaluation grid, by nearest neighbour.

    For the strata only: each 10 m cell takes the value of the product pixel it
    falls in, which is the product's claim at that point. Metrics stay on each
    product's native grid (evaluate.py)."""
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    out = np.full((grid["height"], grid["width"]), NODATA, dtype="uint8")
    reproject(source=comp["array"], destination=out,
              src_transform=comp["transform"], src_crs=comp["crs"],
              src_nodata=NODATA, dst_transform=grid["transform"],
              dst_crs=grid["crs"], dst_nodata=NODATA,
              resampling=Resampling.nearest)
    return out


def _classes_on_grid(path: Path, classes: list[int], grid: dict[str, Any]) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    out = np.zeros((grid["height"], grid["width"]), dtype="uint8")
    with rasterio.open(path) as src:
        reproject(source=rasterio.band(src, 1), destination=out,
                  dst_transform=grid["transform"], dst_crs=grid["crs"],
                  resampling=Resampling.nearest, dst_nodata=0)
    return np.isin(out, classes)


def frame(cfg: dict[str, Any]) -> dict[str, Any]:
    """The stratification for a loaded config, without drawing or writing.

    `build` freezes it; the pilot report reads it to project the main sample
    over the frame it would actually get."""
    from . import domain as domain_mod, reference as reference_mod
    from .align import NotEvaluated, composite
    from .config import resolve

    scfg = cfg.get("sample") or {}
    grid = reference_mod.grid_for(cfg["aoi"]["bbox"], cfg["grid"]["crs"],
                                  float(cfg["grid"]["resolution_m"]))
    ref = reference_mod.ingest(cfg, grid)
    dom = domain_mod.build(cfg, grid, ref)

    products: dict[str, np.ndarray] = {}
    product_meta: dict[str, Any] = {}
    for key, spec in (cfg.get("products") or {}).items():
        try:
            comp = composite(cfg, key, spec)
        except NotEvaluated as exc:
            product_meta[key] = {"status": "not evaluated", "reason": str(exc)}
            continue
        arr = _on_grid(comp, grid)
        arr[~dom["domain"]] = NODATA
        products[key] = arr
        product_meta[key] = {"status": "in the frame", "label": spec.get("label", key),
                             "platforms": spec.get("platforms", []),
                             "observations": comp["observations"],
                             "native_resolution": list(comp["resolution"])}
    if not products:
        raise ValueError("no product could be read; a frame with no product has no S1 "
                         "and no observed split")

    water = resolve(cfg, cfg["domain"]["permanent_water"]["raster"])
    ever_wet = _classes_on_grid(water, list(scfg.get("jrc_ever_wet_classes", [])), grid)
    buffer_m = float(scfg["buffer_m"])
    buffer_px = int(round(buffer_m / grid["resolution"]))
    code = stratify(dom["domain"], dom["reference"], products, ever_wet, buffer_px)
    counts = {k: int(np.count_nonzero(code == v)) for k, v in CODES.items()}
    return {"grid": grid, "domain": dom, "products": products,
            "product_meta": product_meta, "water": water, "buffer_m": buffer_m,
            "code": code, "counts": counts,
            "pixel_km2": grid["resolution"] ** 2 / 1e6}


def build(config_path: str | Path, kind: str) -> dict[str, Any]:
    """Build, draw and freeze one sample (`kind` = "pilot" or "main")."""
    import rasterio
    import yaml
    from rasterio.warp import transform

    from .config import load, resolve

    cfg = load(config_path)
    scfg = cfg.get("sample") or {}
    if kind not in scfg:
        raise ValueError(f"sample.{kind} is not in the config; nothing is assumed")
    plan = scfg[kind]
    out_dir = Path(cfg["_dir"]) / "sample" / kind
    manifest_path = out_dir / "manifest.yaml"
    if manifest_path.exists():
        raise FileExistsError(f"{manifest_path} exists: this sample is frozen and is "
                              "never redrawn. Use a new kind or remove it deliberately.")

    fr = frame(cfg)
    grid, dom, products = fr["grid"], fr["domain"], fr["products"]
    product_meta, water, buffer_m = fr["product_meta"], fr["water"], fr["buffer_m"]
    code, counts, pixel_km2 = fr["code"], fr["counts"], fr["pixel_km2"]
    per_stratum, notes = allocate(counts, plan["allocation"], float(scfg["observed_share"]))
    rng = np.random.default_rng(int(plan["seed"]))
    picks = draw(code, per_stratum, rng)
    order = rng.permutation(len(picks)) + 1

    out_dir.mkdir(parents=True, exist_ok=True)
    width = grid["width"]
    t = grid["transform"]
    rows = [c // width for _, c in picks]
    cols = [c % width for _, c in picks]
    xs = [t.c + (col + 0.5) * t.a for col in cols]
    ys = [t.f + (row + 0.5) * t.e for row in rows]
    lons, lats = transform(grid["crs"], "EPSG:4326", xs, ys)

    points = []
    for i, ((stratum, _), x, y, lon, lat, row, col) in enumerate(
            zip(picks, xs, ys, lons, lats, rows, cols)):
        area = counts[stratum] * pixel_km2
        n_h = per_stratum[stratum]
        points.append({
            "point_id": f"{kind[0].upper()}{i + 1:04d}", "stratum": stratum,
            "x": round(x, 2), "y": round(y, 2), "lon": round(lon, 6), "lat": round(lat, 6),
            "stratum_area_km2": round(area, 4), "n_h": n_h,
            "weight_km2": round(area / n_h, 6), "draw_order": int(order[i]),
            "_rc": (row, col)})

    with open(out_dir / "sample_points.csv", "w", newline="", encoding="utf-8") as f:
        fields = [k for k in points[0] if not k.startswith("_")]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in sorted(points, key=lambda p: p["draw_order"]):
            w.writerow({k: p[k] for k in fields})

    # Hidden from the labelling path: what each map says at each point.
    names = {FLOOD: "flood", NONFLOOD: "not_flood", NODATA: "no_data"}
    with open(out_dir / "product_values.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["point_id", "reference", *products])
        for p in points:
            r, c = p["_rc"]
            w.writerow([p["point_id"], names[int(dom["reference"][r, c])],
                        *(names[int(products[k][r, c])] for k in products)])

    profile = {"driver": "GTiff", "width": grid["width"], "height": grid["height"],
               "count": 1, "dtype": "uint8", "crs": grid["crs"],
               "transform": grid["transform"], "compress": "deflate", "nodata": 0}
    with rasterio.open(out_dir / "strata.tif", "w", **profile) as dst:
        dst.write(code, 1)
        # Provenance in the file (BUG-020). The strata are derived; the dates
        # are the inputs' own, recorded in sample.json beside this file.
        from ...dating import TAG_DATED, TAG_SOURCE, TAG_TOOL

        dst.update_tags(**{
            TAG_SOURCE: "stratified sample frame derived from the reference map and "
                        + ", ".join(sorted(products)),
            TAG_DATED: "derived: no acquisition of its own; the inputs' dates are in "
                       "sample.json",
            TAG_TOOL: f"validation.sample.build ({kind})"})

    # Usable points per product: observed by it, and in its flood / dry class.
    usable = {}
    for key, arr in products.items():
        vals = np.array([arr[p["_rc"]] for p in points])
        usable[key] = {"flood": int((vals == FLOOD).sum()),
                       "not_flood": int((vals == NONFLOOD).sum()),
                       "no_data": int((vals == NODATA).sum())}
        if kind == "main" and min(usable[key]["flood"], usable[key]["not_flood"]) < 50:
            notes.append(f"{key}: fewer than 50 usable points in flood or in dry ground "
                         f"({usable[key]}); raise the allocation before labelling")

    zip_path = resolve(cfg, cfg["reference"]["zip"])
    manifest = {
        "experiment": cfg.get("event"), "kind": kind,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": int(plan["seed"]),
        "design": "docs/sample_reference.md",
        "window": list(cfg["temporal"]["window"]),
        "peak_date": cfg["temporal"].get("peak_date"),
        "tolerance_days": cfg["temporal"].get("tolerance_days"),
        "grid": {"crs": grid["crs"], "resolution_m": grid["resolution"],
                 "shape": [grid["height"], grid["width"]]},
        "domain": dom["census"],
        "strata": {k: {"cells": counts[k], "area_km2": round(counts[k] * pixel_km2, 4),
                       "points": per_stratum.get(k, 0)} for k in CODES},
        "allocation_planned": dict(plan["allocation"]),
        "observed_share": float(scfg["observed_share"]),
        "buffer_m": buffer_m,
        "buffer_note": "provisional, TODO(sample-buffer): no source recorded yet",
        "jrc_ever_wet_classes": list(scfg.get("jrc_ever_wet_classes", [])),
        "products": product_meta,
        "usable_points": usable,
        "notes": notes,
        "inputs": {
            "config": {"path": cfg["_path"], "sha256": _sha256(Path(cfg["_path"]))},
            "reference_zip": {"path": str(zip_path), "sha256": _sha256(zip_path)},
            "permanent_water": {"path": str(water), "sha256": _sha256(water)},
        },
        "label_key_version": "v0-pilot" if kind == "pilot" else "v1",
        "files": ["sample_points.csv", "product_values.csv", "strata.tif"],
    }
    # Written last: its presence is what "frozen" means.
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return {"out_dir": str(out_dir), "points": len(points), "strata": manifest["strata"],
            "usable_points": usable, "notes": notes, "products": product_meta,
            "domain_km2": dom["census"]["domain_km2"]}


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 3 or sys.argv[2] not in ("pilot", "main"):
        raise SystemExit("usage: python -m core.workflows.validation.sample <config.yaml> pilot|main")
    print(json.dumps(build(sys.argv[1], sys.argv[2]), indent=2, default=str))
