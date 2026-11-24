"""accuracy_metrics: agreement of the NRT flood products with a reference map.

Monty runs this; Entro reads the table it produces and explains it
(`agents/entro/workflows/accuracy_interpretation.md`). The steps live in
`core/workflows/validation/`; this module only runs them in order and writes
the payload.

**Agreement, not accuracy.** The reference (UNOSAT FL20251128LKA for the Kelani
event) is an operational map marked "not yet field validated". Every table,
note and payload key says "agreement with the reference".

Everything comes from one `config.yaml` -- see
`docs/accuracy_metrics.config.example.yaml`. A product that cannot be compared
keeps its row with the reason (not evaluated), so the metric order is never
shown as complete when it is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import workflow


@workflow(
    "accuracy_metrics",
    "Agreement of the NRT flood products with a reference flood map, from one "
    "config.yaml. Per product, on its native grid: coverage, precision, recall, F1, "
    "IoU, MCC and the confusion matrix, in one table sorted by F1 with the TOPSIS "
    "rank beside it. Agreement with the reference, not accuracy against ground "
    "truth. Leave `config` empty to use the reference set up for the session's "
    "AOI (Kelani basin, the main experiment: Copernicus EMS EMSR851 AOI02; "
    "Gampola: EMSR851 AOI09, or `reference`=\"emsr851-aoi03\"; lower Kelani: UNOSAT); "
    "an AOI with none fails with that reason. "
    "`aoi_bbox` optionally narrows the AOI to test a small window.",
    label="Agreement with a reference flood map",
    source="Copernicus EMS EMSR851 / UNOSAT reference maps; product STAC catalogues; JRC GSW; Copernicus DEM WBM",
    order=40,
    data_source_uncertainty=(
        "What: the reference (Copernicus EMS or UNOSAT) is an operational map "
        "from its own date and sensor, photo-interpreted, not field-validated, which is why the result is agreement with the "
        "reference, not accuracy. Affects: every F1, IoU and MCC value, and their "
        "order. Reduce: the stratified labelled sample (Stehman estimates) already "
        "in the repo, and matching the reference date to the product passes."),
    method_uncertainty=(
        "What: the reference is aggregated to each product's own grid inside a "
        "domain that excludes cloud, sea and permanent water, so products are "
        "compared over different covered areas. Affects: a product with low "
        "coverage can score well over a small area. Reduce: read coverage beside "
        "each metric (reported), and compare on the area all products observed."),
)
def accuracy_metrics(config: str = "", aoi_bbox: list[float] | None = None,
                     reference: str = "", event_id: str = "") -> dict[str, Any]:
    """Run the validation plan end to end and return the table and its evidence.

    Args:
        config: path to config.yaml (absolute, or relative to the working
            directory). Empty: the reference hardcoded for the session's AOI
            (core/references.py, user decision 2026-11-18).
        aoi_bbox: [west, south, east, north] to run on a smaller window first,
            as the plan asks before the full basin. Recorded in the payload.
        reference: with no `config`, which of the AOI's references to use by
            name ("emsr851-aoi02", "emsr851-aoi09", "emsr851-aoi03", "unosat");
            empty = primary.
        event_id: accepted and ignored. Argus copies the batch's event_id into
            every task's params; without it here the first call of a console
            turn failed as signature_mismatch (found 2026-11-18).
    """
    from .. import references, session
    from .validation import align, config as cfg_mod, domain, evaluate, reference as ref_mod, table

    # The reference is fixed per experiment (core/references.py, user decision
    # 2026-11-18) and the agents call this from a chat, so nothing they might
    # send stops it: a named `reference` wins over `config`, a `config` path
    # that is not there falls back to the AOI's own reference (logged in
    # `reference_choice`), and an `aoi_bbox` that IS the experiment's AOI is
    # not treated as a small test window.
    record = session.current()
    session_bbox = list(record.place.bbox) if record is not None and record.place.bbox else None
    picked: dict[str, Any] | None = None
    fallback = ""
    if config and not reference and not Path(config).is_file():
        fallback = f"the config {config!r} does not exist; the AOI's reference was used"
        config = ""
    # A session whose AOI IS an experiment uses that experiment's references:
    # an existing config of ANOTHER experiment (the old lower-Kelani UNOSAT
    # one, say) would run the wrong reference over the wrong box.
    own = references.experiment_for(session_bbox)
    if config and own and not references.belongs(config, own):
        fallback = (f"the config {config!r} is not one of {own}'s references; "
                    "the AOI's reference was used")
        config = ""
    if not config or reference:
        # Raises NoReference with a plain reason: never a guessed reference.
        picked = references.resolve(session_bbox or aoi_bbox, reference)
        config = picked["path"]
        if fallback:
            picked["note"] = fallback
        if aoi_bbox and references.same_box(aoi_bbox, picked["bbox"]):
            aoi_bbox = None
    cfg = cfg_mod.load(config)
    small_window = None
    if aoi_bbox:
        if len(aoi_bbox) != 4:
            raise ValueError("aoi_bbox must be [west, south, east, north]")
        small_window = [float(v) for v in aoi_bbox]
        cfg["aoi"]["bbox"] = small_window

    grid = ref_mod.grid_for(cfg["aoi"]["bbox"], cfg["grid"]["crs"],
                              float(cfg["grid"]["resolution_m"]))
    ref = ref_mod.ingest(cfg, grid)
    dom = domain.build(cfg, grid, ref)

    results: list[dict[str, Any]] = []
    for key, spec in cfg["products"].items():
        row: dict[str, Any] = {"id": key, "label": spec.get("label", key),
                               "sensor": spec.get("sensor", "")}
        try:
            product = align.composite(cfg, key, spec)
            row["metrics"] = evaluate.on_native_grid(product, grid, dom, cfg)
            row["product"] = {"crs": str(product["crs"]),
                              "resolution": list(product["resolution"]),
                              "shape": list(product["shape"]),
                              "window": product["window"],
                              "observations": product["observations"],
                              "observations_with_data": product["observations_with_data"],
                              "encoding": spec.get("encoding"),
                              "nodata_masks": [m.get("asset") or m.get("path")
                                               for m in spec.get("nodata_masks") or []]}
        except align.NotEvaluated as exc:
            row["reason"] = str(exc)
        except Exception as exc:                               # noqa: BLE001
            # A failure is reported in the product's row, never hidden: the
            # other products are still evaluated.
            row["reason"] = f"failed: {type(exc).__name__}: {exc}"[:300]
        results.append(row)

    topsis_from = _session_ranking(cfg, record)
    topsis = table.topsis_ranks(cfg)
    rows = table.build(results, topsis)
    note = table.note_line(cfg, results)
    out_dir = Path(cfg_mod.resolve(cfg, cfg["output_dir"]))
    if small_window:
        out_dir = out_dir / "small_window"
    paths = table.write(rows, note, out_dir)

    evaluated = [r for r in rows if r["status"] == "evaluated" and r["metric_rank"]]
    payload: dict[str, Any] = {
        "framing": "agreement with the reference, not accuracy against ground truth",
        "reference": ref["meta"],
        # Which hardcoded reference was used, and which others the AOI has.
        "reference_choice": picked or {"config": cfg["_path"], "how": "config passed"},
        "temporal": cfg["temporal"],
        "grid_mode": "native: the reference is aggregated to each product's own pixels",
        "evaluation": cfg["evaluation"],
        "aoi": {"bbox": cfg["aoi"]["bbox"], "small_window": bool(small_window),
                "shape": "rectangle (bbox), not the basin outline"},
        "domain": dom["census"],
        "assumptions": cfg.get("assumptions") or [],
        "table": rows,
        "note": note,
        "products": results,
        "topsis_source": topsis_from or str(cfg_mod.resolve(cfg, (cfg.get("topsis") or {}).get("path")) or ""),
        "ranks_compared": {
            "evaluated_products": len(evaluated),
            "metric_order": [r["id"] for r in evaluated],
            "topsis_order_of_those": [r["id"] for r in sorted(
                (r for r in evaluated if r["topsis_rank"]), key=lambda r: r["topsis_rank"])],
            "caveat": ("a rank comparison over fewer than three evaluated products "
                       "says nothing about consistency") if len(evaluated) < 3 else "",
        },
        "outputs": paths,
        "seed": cfg["seed"],
        "config": cfg["_path"],
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out_dir / "agreement.json").write_text(json.dumps(payload, indent=1, default=str),
                                            encoding="utf-8")
    payload["outputs"]["payload"] = str(out_dir / "agreement.json")
    return payload


def _session_ranking(cfg: dict[str, Any], record: Any) -> str:
    """Point the config's TOPSIS rank at the session's own stored ranking when
    the config names none -- run from the console, the ranking this session
    computed is the one to set beside the agreement order. Returns what was
    used, or "" (the config's own setting, or no rank column)."""
    if (cfg.get("topsis") or {}).get("path") or record is None:
        return ""
    from .. import session_store
    from ..outputs import Inputs

    store = session_store.for_record(record)
    if store is None:
        return ""
    name, payload = Inputs(store).get_named("ranking")
    path = Path(store.inputs_dir) / f"{name}.json" if name else None
    if not payload or path is None or not path.is_file():
        return ""
    cfg["topsis"] = {**(cfg.get("topsis") or {}), "path": str(path)}
    return f"this session's stored ranking ({name})"
