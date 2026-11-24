"""The pilot report: what 50 labelled points say about the main sample.

Design: docs/sample_reference.md ("The pilot"). Reads the frozen pilot
(`sample/pilot/`: manifest, points, product values, images, labels) and writes
`report.json` and `report.md` beside it. It measures:

- time per point, and how often each image could be seen;
- the point labels (cumulative rule, `estimate.point_label`), per stratum;
- indicative estimates -- flood area, and each map's precision, recall, IoU,
  F1 and coverage -- with Stehman (2014) standard errors. Fifty points; these
  are for planning, never for the thesis table;
- a projection of the main sample: for candidate sizes and allocations over
  the frame the main sample WOULD get from the current config (products added
  since the pilot froze included), the interval each estimate would have.

Maps added after the pilot froze are scored on the same points: Stehman's
estimators do not need the strata to be any map's classes, which is why a
product can join after the freeze. Their values at the points are read from
the current frame and the report says which ones were.

Run:  python -m core.workflows.validation.pilot <config.yaml>
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate as est
from .metrics import FLOOD, NODATA, NONFLOOD
from .sample import CODES, STRATA, allocate

NAMES = {FLOOD: "flood", NONFLOOD: "not_flood", NODATA: "no_data"}
SIZES = (300, 450, 600)
REPS = 1000


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _offset(text: str | None) -> timedelta:
    if not text:
        return timedelta(0)
    sign = -1 if text.startswith("-") else 1
    hh, mm = text.lstrip("+-").split(":")
    return sign * timedelta(hours=int(hh), minutes=int(mm))


def _rc(grid: dict[str, Any], x: float, y: float) -> tuple[int, int]:
    t = grid["transform"]
    return int(math.floor((y - t.f) / t.e)), int(math.floor((x - t.c) / t.a))


# -- per-point table ---------------------------------------------------------
def point_table(points, values, labels, maps, peak, offset):
    """One record per point: stratum, overall label, and per map (r, resolved, m, c)."""
    by_point = est.group(labels)
    table = []
    for p in points:
        obs = by_point.get(p["point_id"], [])
        overall = est.point_label(obs, peak, offset)
        rec = {"point_id": p["point_id"], "stratum": p["stratum"],
               "label": overall["label"], "reason": overall.get("reason"),
               "maps": {}}
        for key, spec in maps.items():
            mine = obs if spec["independent"] is None else est.independent_of(obs, spec["platforms"])
            lab = est.point_label(mine, peak, offset)
            v = values[p["point_id"]][key]
            rec["maps"][key] = {"resolved": lab["label"] != "unresolved",
                                "r": int(lab["label"] == "flood"),
                                "m": int(v == "flood"), "c": int(v != "no_data")}
        table.append(rec)
    return table


def _strata_args(table, cells, key=None):
    """(strata, per-stratum (r, m, c) lists) for `est.accuracy`, resolved points only."""
    strata = {h: {"N": n} for h, n in cells.items() if n > 0}
    pts: dict[str, list] = {h: [] for h in strata}
    for rec in table:
        mp = rec["maps"][key]
        if mp["resolved"] and rec["stratum"] in pts:
            pts[rec["stratum"]].append((mp["r"], mp["m"], mp["c"]))
    return strata, pts


def area_estimate(table, cells, pixel_km2):
    strata = {h: {"N": n} for h, n in cells.items() if n > 0}
    y = defaultdict(list)
    for rec in table:
        if rec["label"] != "unresolved" and rec["stratum"] in strata:
            y[rec["stratum"]].append(1.0 if rec["label"] == "flood" else 0.0)
    out = est.total(strata, y)
    for k in ("estimate", "se"):
        if out.get(k) is not None:
            out[k] *= pixel_km2
    if out.get("ci95"):
        out["ci95"] = [v * pixel_km2 for v in out["ci95"]]
    return out


# -- projection -----------------------------------------------------------
def _pool(table, main_stratum_of, h):
    """Pilot points that stand for main stratum h, or the pilot's own h."""
    pts = [r for r in table if main_stratum_of[r["point_id"]] == h]
    if len([r for r in pts if r["label"] != "unresolved"]) >= 3:
        return pts, "pilot points now inside this stratum"
    pts = [r for r in table if r["stratum"] == h]
    if len([r for r in pts if r["label"] != "unresolved"]) >= 3:
        return pts, "too few pilot points fall inside it; the pilot's own stratum of the same name stands in"
    return [], "no pilot information"


def project(table, main_cells, main_stratum_of, maps, plans, pixel_km2, seed=7):
    """Monte Carlo of the main sample under each plan, from the pilot's points.

    Each main stratum is resampled from the pilot points that stand for it (a
    bootstrap), smoothed so no stratum is projected with zero variance: every
    resolved point is also present with its label flipped, at a total weight
    of one point per stratum. Unresolved points are drawn at their pilot rate,
    so the projection already pays for cloud."""
    rng = np.random.default_rng(seed)
    pools, notes = {}, {}
    for h, n in main_cells.items():
        if n == 0:
            continue
        pts, why = _pool(table, main_stratum_of, h)
        notes[h] = {"basis": why, "pilot_points": len(pts)}
        if not pts:
            continue
        items, weights = [], []
        resolved = [r for r in pts if r["label"] != "unresolved"]
        for r in pts:
            items.append((r, False)); weights.append(1.0)
        for r in resolved:
            items.append((r, True)); weights.append(1.0 / len(resolved))
        w = np.asarray(weights)
        pools[h] = (items, w / w.sum())
    out = {}
    for name, per_stratum in plans.items():
        draws = defaultdict(list)
        for _ in range(REPS):
            sample = {}
            for h, n in per_stratum.items():
                if n and h in pools:
                    items, p = pools[h]
                    idx = rng.choice(len(items), size=n, replace=True, p=p)
                    sample[h] = [items[i] for i in idx]
            strata = {h: {"N": main_cells[h]} for h in sample}
            y = {h: [(1.0 if (not flip) == (r["label"] == "flood") else 0.0)
                     for r, flip in s if r["label"] != "unresolved"] for h, s in sample.items()}
            a = est.total(strata, y)
            draws["flood_area_km2"].append(None if a["estimate"] is None else a["estimate"] * pixel_km2)
            for key in maps:
                pts = {}
                for h, s in sample.items():
                    pts[h] = []
                    for r, flip in s:
                        mp = r["maps"][key]
                        if mp["resolved"]:
                            rr = mp["r"] if not flip else 1 - mp["r"]
                            pts[h].append((rr, mp["m"], mp["c"]))
                acc = est.accuracy(strata, pts)
                for m in ("precision", "recall"):
                    draws[f"{key}.{m}"].append(acc[m]["estimate"])
                draws[f"{key}.map_flood_points"].append(acc["usable_points"]["map_flood"])
        res = {}
        for k, v in draws.items():
            vals = np.array([x for x in v if x is not None], dtype=float)
            undefined = len(v) - len(vals)
            if k.endswith("map_flood_points"):
                res[k] = {"median": float(np.median(vals)) if len(vals) else None}
                continue
            res[k] = {"half_width_95": float(est.Z95 * vals.std(ddof=1)) if len(vals) > 1 else None,
                      "mean": float(vals.mean()) if len(vals) else None,
                      "undefined_share": undefined / len(v)}
        out[name] = {"allocation": per_stratum, "n": int(sum(per_stratum.values())), "result": res}
    return out, notes


def plans_for(main_cells, pilot_shares, unresolved, observed_share):
    """Candidate allocations, each at SIZES. Base strata S1-S4, split by allocate()."""
    base_cells = {s: sum(v for k, v in main_cells.items() if k.split(".")[0] == s) for s in STRATA}
    plans = {}
    for n in SIZES:
        shapes = {
            "current 2:2:2:3": {"S1": 2, "S2": 2, "S3": 2, "S4": 3},
            "equal": {s: 1 for s in STRATA},
        }
        # Neyman for the flood area: n_h ~ N_h * S_h, S_h from the (smoothed)
        # pilot flood share, inflated for the pilot's unresolved rate, with at
        # least 50 points per stratum so every stratum keeps a usable estimate.
        ney = {}
        for s in STRATA:
            p = pilot_shares.get(s)
            sd = math.sqrt(p * (1 - p)) if p is not None else 0.5
            ney[s] = base_cells[s] * sd / max(1e-9, 1 - unresolved.get(s, 0.0))
        shapes["Neyman (area), floor 50"] = ney
        for label, shape in shapes.items():
            tot = sum(shape[s] for s in STRATA if base_cells[s] > 0)
            alloc = {s: (int(round(n * shape[s] / tot)) if base_cells[s] > 0 else 0) for s in STRATA}
            if label.startswith("Neyman"):
                floor = {s: 50 for s in STRATA if base_cells[s] > 0}
                rest = n - sum(floor.values())
                extra = {s: max(0.0, shape[s]) for s in floor}
                tot_e = sum(extra.values()) or 1.0
                alloc = {s: floor.get(s, 0) + int(round(rest * extra.get(s, 0) / tot_e)) for s in STRATA}
            per, _ = allocate(main_cells, alloc, observed_share)
            plans[f"{label} @ {n}"] = {"base": alloc, "design": per}
    return plans


# -- report -----------------------------------------------------------------
def build(config_path: str | Path) -> dict[str, Any]:
    import yaml

    from .config import load
    from .sample import frame

    cfg = load(config_path)
    pdir = Path(cfg["_dir"]) / "sample" / "pilot"
    manifest = yaml.safe_load((pdir / "manifest.yaml").read_text(encoding="utf-8"))
    points = _read_csv(pdir / "sample_points.csv")
    values = {r["point_id"]: r for r in _read_csv(pdir / "product_values.csv")}
    images = json.loads((pdir / "images.json").read_text(encoding="utf-8"))
    rows = _read_csv(pdir / "labels.csv")
    offset = _offset(images.get("utc_offset_of_window"))
    peak = date.fromisoformat(str(cfg["temporal"]["peak_date"]))

    labellers = Counter((r["labeller"], r["round"]) for r in rows)
    primary = [r for r in rows if r["round"] == "primary"]
    per_point_labellers = {pid: {r["labeller"] for r in rs} for pid, rs in est.group(primary).items()}
    if any(len(v) > 1 for v in per_point_labellers.values()):
        raise ValueError("more than one primary labeller on a point; the pilot report "
                         "expects one primary labeller (a QA round is separate)")
    labelled = {r["point_id"] for r in primary}
    unlabelled = [p["point_id"] for p in points if p["point_id"] not in labelled]

    # The frame the main sample would get now, and every map's value at the
    # pilot points -- frozen values where the pilot has them.
    fr = frame(cfg)
    grid = fr["grid"]
    main_stratum_of = {}
    mismatch = defaultdict(int)
    late = [k for k in fr["products"] if k not in next(iter(values.values()))]
    for p in points:
        r, c = _rc(grid, float(p["x"]), float(p["y"]))
        code = int(fr["code"][r, c])
        main_stratum_of[p["point_id"]] = next((k for k, v in CODES.items() if v == code), "outside")
        for k, arr in fr["products"].items():
            now = NAMES[int(arr[r, c])]
            if k in late:
                values[p["point_id"]][k] = now
            elif values[p["point_id"]][k] != now:
                mismatch[k] += 1

    maps = {"reference": {"label": f"UNOSAT {cfg['reference'].get('code', '')}".strip(),
                          "platforms": ["sentinel-2"], "independent": None,
                          "note": "the reference is a Sentinel-2 merge; it is scored on all labels, "
                                  "which share its source -- agreement is inflated, and said so"}}
    for k, spec in (cfg.get("products") or {}).items():
        if k in fr["products"]:
            maps[k] = {"label": spec.get("label", k), "platforms": spec.get("platforms", []),
                       "independent": True, "added_after_freeze": k in late}
    table = point_table(points, values, primary, maps, peak, offset)

    # Timing: one elapsed_s per point (both image rows carry it).
    secs = [float(rs[0]["elapsed_s"]) for rs in est.group(primary).values() if rs[0].get("elapsed_s")]
    stamps = sorted(datetime.fromisoformat(r["labelled_at_utc"]) for r in primary)

    # Observability per image, per stratum.
    stratum_of = {p["point_id"]: p["stratum"] for p in points}
    per_image = defaultdict(lambda: Counter())
    for r in primary:
        cls = r["class"] if r["class"] != "unobservable" else f"unobservable:{r['unobservable_reason']}"
        per_image[(r["image_datetime_utc"][:10], r["source"], r["image_id"])][cls] += 1

    pilot_cells = {k: int(v["cells"]) for k, v in manifest["strata"].items()}
    per_stratum = {}
    for h in pilot_cells:
        recs = [t for t in table if t["stratum"] == h]
        if not recs:
            continue
        cnt = Counter(t["label"] for t in recs)
        res = sum(v for k, v in cnt.items() if k != "unresolved")
        per_stratum[h] = {"points": len(recs), "labels": dict(cnt),
                          "area_km2": pilot_cells[h] * fr["pixel_km2"],
                          "flood_share_of_resolved": (cnt["flood"] / res) if res else None,
                          "unresolved_share": cnt["unresolved"] / len(recs)}

    pixel_km2 = fr["pixel_km2"]
    estimates = {"flood_area_km2": area_estimate(table, pilot_cells, pixel_km2), "maps": {}}
    for key, spec in maps.items():
        strata, pts = _strata_args(table, pilot_cells, key)
        acc = est.accuracy(strata, pts)
        resolved = sum(1 for t in table if t["maps"][key]["resolved"])
        estimates["maps"][key] = {"label": spec["label"], "resolved_points": resolved,
                                  "independent_labels": spec["independent"],
                                  "added_after_freeze": spec.get("added_after_freeze", False),
                                  **acc}

    # Projection over the main frame.
    main_cells = {k: v for k, v in fr["counts"].items()}
    base_share, base_unres = {}, {}
    for s in STRATA:
        recs = [t for t in table if main_stratum_of[t["point_id"]].split(".")[0] == s] or \
               [t for t in table if t["stratum"].split(".")[0] == s]
        res = [t for t in recs if t["label"] != "unresolved"]
        if res:
            k = sum(t["label"] == "flood" for t in res)
            base_share[s] = (k + 0.5) / (len(res) + 1)
        base_unres[s] = (sum(t["label"] == "unresolved" for t in recs) + 0.5) / (len(recs) + 1) if recs else 0.0
    plans = plans_for(main_cells, base_share, base_unres, float(cfg["sample"]["observed_share"]))
    proj_maps = {k: m for k, m in maps.items()}
    projection, pool_notes = project(table, main_cells, main_stratum_of, proj_maps,
                                     {k: v["design"] for k, v in plans.items()}, pixel_km2)
    for k in projection:
        projection[k]["base"] = plans[k]["base"]

    report = {
        "experiment": cfg.get("event"), "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pilot": {"frozen_at": manifest["frozen_at"], "seed": manifest["seed"],
                  "window": manifest["window"], "peak_date": manifest["peak_date"],
                  "points": len(points), "unlabelled": unlabelled,
                  "labellers": {f"{a}/{b}": n for (a, b), n in labellers.items()},
                  "label_rows": len(primary), "buffer_m": manifest["buffer_m"]},
        "timing": {"points_timed": len(secs),
                   "median_s": float(np.median(secs)) if secs else None,
                   "mean_s": float(np.mean(secs)) if secs else None,
                   "p90_s": float(np.percentile(secs, 90)) if secs else None,
                   "session_minutes": ((stamps[-1] - stamps[0]).total_seconds() / 60) if len(stamps) > 1 else None},
        "images": [{"date": d, "source": s, "image_id": i, "classes": dict(c)}
                   for (d, s, i), c in sorted(per_image.items())],
        "strata": per_stratum,
        "estimates": estimates,
        "low_confidence": [{"point_id": r["point_id"], "image": r["image_datetime_utc"][:10],
                            "class": r["class"], "confidence": r["confidence"],
                            "notes": r.get("notes", "")}
                           for r in primary if r["confidence"] == "1" or r.get("notes")],
        "frame_check": {"maps_added_after_freeze": late, "frozen_value_mismatches": dict(mismatch)},
        "main_frame": {"strata_km2": {k: v * pixel_km2 for k, v in main_cells.items()},
                       "products": {k: v.get("status") for k, v in fr["product_meta"].items()},
                       "pilot_points_by_main_stratum": dict(Counter(main_stratum_of.values()))},
        "projection": projection, "projection_basis": pool_notes,
        "projection_inputs": {"flood_share_smoothed": base_share, "unresolved_share_smoothed": base_unres,
                              "reps": REPS},
    }
    (pdir / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (pdir / "report.md").write_text(render(report, cfg), encoding="utf-8")
    return report


def _f(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


def _ci(e, nd=3):
    if e.get("estimate") is None:
        return f"n/a ({e.get('undefined', 'undefined')})"
    if e.get("se") is None:
        return f"{e['estimate']:.{nd}f} (no SE: a stratum has < 2 resolved points)"
    lo, hi = e["ci95"]
    return f"{e['estimate']:.{nd}f} +/- {est.Z95 * e['se']:.{nd}f} ({lo:.{nd}f} to {hi:.{nd}f})"


def render(r: dict[str, Any], cfg: dict[str, Any]) -> str:
    p, t = r["pilot"], r["timing"]
    L = [f"# Pilot report -- {r['experiment']}", "",
         f"Written {r['written']} by `core/workflows/validation/pilot.py` from the frozen pilot "
         f"(frozen {p['frozen_at']}, seed {p['seed']}). Design: `docs/sample_reference.md`.", "",
         "**Fifty points. Every estimate below is for planning the main sample, not for the "
         "thesis table.**", "",
         "## What was labelled", "",
         f"- Window {p['window'][0]} to {p['window'][1]} (local), peak day {p['peak_date']}.",
         f"- {p['points']} points, {p['label_rows']} label rows; labeller/round: "
         + ", ".join(f"`{k}` {v}" for k, v in p["labellers"].items()) + ".",
         f"- Unlabelled points: {', '.join(p['unlabelled']) or 'none'}.", "",
         "## Time per point", "",
         f"Median {_f(t['median_s'], 1)} s, mean {_f(t['mean_s'], 1)} s, 90th percentile "
         f"{_f(t['p90_s'], 1)} s over {t['points_timed']} points (`elapsed_s`, both images "
         f"of a point together). First to last save: {_f(t['session_minutes'], 1)} min.", "",
         "## What each image showed", "",
         "| Date (UTC) | Source | Image | Classes |", "|---|---|---|---|"]
    for im in r["images"]:
        L.append(f"| {im['date']} | {im['source']} | `{im['image_id']}` | "
                 + ", ".join(f"{k} {v}" for k, v in sorted(im["classes"].items())) + " |")
    L += ["", "## Point labels by stratum (cumulative rule)", "",
          "| Stratum | Area km2 | Points | flood | not_flood | preexisting_water | unresolved | "
          "Flood share of resolved |", "|---|---|---|---|---|---|---|---|"]
    for h, s in r["strata"].items():
        lab = s["labels"]
        L.append(f"| {h} | {s['area_km2']:.2f} | {s['points']} | {lab.get('flood', 0)} | "
                 f"{lab.get('not_flood', 0)} | {lab.get('preexisting_water', 0)} | "
                 f"{lab.get('unresolved', 0)} | {_f(s['flood_share_of_resolved'], 2)} |")
    L += ["", "`unresolved` points are excluded and counted; within a stratum they are "
          "assumed missing at random. `preexisting_water` counts as not flood.", "",
          "## Indicative estimates (95% intervals)", "",
          f"- **Flood area:** {_ci(r['estimates']['flood_area_km2'], 2)} km2 "
          f"(domain {sum(s['area_km2'] for s in r['strata'].values()):.2f} km2 in the sampled strata).",
          "", "| Map | Labels | Resolved pts | Map-flood pts | Precision | Recall | IoU | F1 | Coverage |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k, m in r["estimates"]["maps"].items():
        ind = {None: "all (same source)", True: "independent only"}[m["independent_labels"]]
        if m.get("added_after_freeze"):
            ind += "; added after freeze"
        L.append(f"| {m['label']} | {ind} | {m['resolved_points']} | {m['usable_points']['map_flood']} | "
                 + " | ".join(_ci(m[x], 2) for x in ("precision", "recall", "iou", "f1", "coverage")) + " |")
    L += ["", "A map whose labels all come from its own sensor has no resolved point: "
          "its row says why instead of giving a number.", ""]
    if r["low_confidence"]:
        L += ["## Where the label key was hard (confidence 1, or a note)", "",
              "| Point | Image | Class | Confidence | Note |", "|---|---|---|---|---|"]
        for x in r["low_confidence"]:
            L.append(f"| {x['point_id']} | {x['image']} | {x['class']} | {x['confidence']} | {x['notes']} |")
        L.append("")
    fc = r["frame_check"]
    L += ["## The main frame (current config)", "",
          "| Stratum | Area km2 | Pilot points now inside |", "|---|---|---|"]
    for h, a in r["main_frame"]["strata_km2"].items():
        L.append(f"| {h} | {a:.2f} | {r['main_frame']['pilot_points_by_main_stratum'].get(h, 0)} |")
    L += ["", f"Maps added since the pilot froze: {', '.join(fc['maps_added_after_freeze']) or 'none'}. "
          f"Frozen product values that the current frame reads differently: "
          f"{fc['frozen_value_mismatches'] or 'none'}.", "",
          "## Projected main sample", "",
          f"{r['projection_inputs']['reps']} simulated samples per plan, each stratum resampled from the pilot "
          "points that stand for it (smoothed so no stratum has zero variance; unresolved points drawn at "
          "the pilot rate). Half-widths of the 95% interval:", ""]
    keys = ["flood_area_km2"] + [f"{k}.{m}" for k in r["estimates"]["maps"] for m in ("precision", "recall")]
    L.append("| Plan | S1/S2/S3/S4 | " + " | ".join(keys) + " |")
    L.append("|---|---|" + "---|" * len(keys))
    for name, pr in r["projection"].items():
        b = pr["base"]
        cells = []
        for k in keys:
            v = pr["result"].get(k, {})
            hw = v.get("half_width_95")
            cells.append("n/a" if hw is None else f"{hw:.2f}" if k == "flood_area_km2" else f"{hw:.3f}")
        L.append(f"| {name} | {b['S1']}/{b['S2']}/{b['S3']}/{b['S4']} | " + " | ".join(cells) + " |")
    L += ["", "Expected map-flood points (the points a precision rests on), median over simulations:", "",
          "| Plan | " + " | ".join(r["estimates"]["maps"]) + " |", "|---|" + "---|" * len(r["estimates"]["maps"])]
    for name, pr in r["projection"].items():
        L.append(f"| {name} | " + " | ".join(
            _f(pr["result"].get(f"{k}.map_flood_points", {}).get("median"), 0) for k in r["estimates"]["maps"]) + " |")
    L += ["", "Basis per main stratum: " + "; ".join(f"{h}: {v['basis']} ({v['pilot_points']})"
                                                    for h, v in r["projection_basis"].items()) + ".", "",
          "## Assumptions", "",
          f"- S3 buffer {p['buffer_m']:.0f} m: provisional, no source recorded (TODO(sample-buffer)).",
          "- Unresolved points are missing at random within their stratum.",
          "- At about ten points per stratum the linearised (Stehman) standard errors of a ratio run "
          "5-20% low when flood is rare (tests/test_estimate.py): the pilot intervals are optimistic.",
          "- The reference (UNOSAT) is a Sentinel-2 merge and the labels are Sentinel-2: its agreement "
          "with the labels is not independent.",
          "- A map scored after the freeze uses the frame's current read of that map at the points.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m core.workflows.validation.pilot <config.yaml>")
    rep = build(sys.argv[1])
    print(json.dumps({k: rep[k] for k in ("pilot", "timing", "strata")}, indent=1, default=str))
    print(json.dumps(rep["estimates"]["flood_area_km2"], default=str))
