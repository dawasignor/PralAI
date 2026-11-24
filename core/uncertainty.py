"""The steps of every tool call and the uncertainty in each step (RQ6, CP6).

Every tool call becomes a short, numbered list of STEPS -- what the tool did,
in order -- and each step carries the uncertainty items that belong to it,
at one of four levels:

    input     the inherent limits of a dataset        (GLO-30 is a surface model)
    tool      what the processing did to it            (valid fraction, sea excluded)
    pipeline  how the run went                         (a retry, a fallback, cache off)
    decision  how firm the conclusion is               (a rank is not a usability claim)

**Built from the payload the tool returned, never invented.** For a tool that
does not report its own steps, an adapter below reads them off fields the tool
already returns (`selection.attempts`, `qc.gaps`, `coverage.valid_fraction`
...). A field that is absent produces no item; nothing is estimated to fill a
step. A tool that fills its own uncertainty block keeps it -- adapters only
fill the levels it left empty.

**Three readers, one record.**

* the payload itself: `run.steps` and the `uncertainty` block, stored with the
  result, so a session or an experiment keeps its register across turns;
* `runs/<run_id>/steps.jsonl` and `runs/<run_id>/uncertainty.json`, the run's
  own register for RQ6 (and the `tool-step` event stream);
* Entro, through the `read_uncertainty` chat tool, which prints the register
  crisply: one line per step, one line per item. Entro's answer is that
  register, interpreted step by step (agents/entro/workflows/uncertainty_by_step.md).
"""

from __future__ import annotations

import json
from typing import Any, Callable

LEVELS = ("input", "tool", "pipeline", "decision")


def item(level: str, key: str, statement: str, value: Any = None, unit: str = "",
         basis: str = "measured", vintage: str = "") -> dict[str, Any]:
    return {"level": level, "key": key, "statement": statement, "value": value,
            "unit": unit, "basis": basis, "vintage": vintage, "vault_note": ""}


def step(name: str, did: str, *items: dict[str, Any]) -> dict[str, Any]:
    return {"step": name, "did": did, "uncertainty": [i for i in items if i]}


def _g(d: Any, path: str, default: Any = None) -> Any:
    for key in path.split("."):
        if not isinstance(d, dict):
            return default
        d = d.get(key)
    return default if d is None else d


def _pct(x: Any) -> str:
    from .numfmt import share

    return share(x)


def _num(x: Any) -> str:
    from .numfmt import num

    return num(x)


# -- adapters, one per tool ------------------------------------------------------
def _legacy_note(src: dict) -> str:
    from .workflows.weather_grid import model_of

    return model_of(src)["note"]


def _weather(p: dict, params: dict) -> list[dict]:
    src = _g(p, "meta.source", {})
    warnings = _g(p, "qc.warnings", []) or []
    gaps = _g(p, "qc.gaps", {}) or {}
    steps = [step("request", f"{_g(p, 'grid.n_points')} grid points at "
                  f"{_g(p, 'grid.spacing_deg')} deg from "
                  f"{src.get('model_id') or 'a model the archive chose'}, window "
                  f"{' to '.join(_g(p, 'meta.window_utc', []) or [])}",
                  item("input", "model", f"The weather is {src.get('model_id')} "
                       f"({src.get('native_res_km')} km): a model archive, not observations.",
                       src.get("native_res_km"), "km", "stated_by_source",
                       str(src.get("retrieved_at", ""))[:10]) if src.get("model_id") else
                  item("input", "model_label", _legacy_note(src), basis="measured"),
                  *[item("tool", "oversampling", w) for w in warnings if "finer than" in w])]
    steps.append(step("aggregate", "hourly mean / max / p90 / area fraction across the grid "
                      "points; storm totals, antecedent index and rain bursts",
                      item("tool", "no_land_mask", "Grid points over the sea, if any, are "
                           "weighted equally with land: the grid is not land-masked.",
                           basis="assumed"),
                      *[item("input", "gate_resolution", w) for w in warnings if "used to gate" in w]))
    if gaps:
        withheld = gaps.get("withheld") or []
        steps.append(step("completeness", "every hour checked for a value at every point",
                          item("tool", "gaps", "Complete: no hour is missing." if gaps.get("window_complete")
                               else f"Gaps: {len(withheld)} figure(s) withheld rather than filled.",
                               len(withheld), "withheld")))
    forecast = [w for w in warnings if "FORECAST" in w]
    if forecast:
        steps.append(step("forecast hours", "hours at or after retrieval flagged",
                          *[item("tool", "forecast", w) for w in forecast]))
    return steps


def _lulc(p: dict, params: dict) -> list[dict]:
    sel, scene = p.get("selection") or {}, p.get("scene") or {}
    cov = _g(p, "indices.ndvi.stats.coverage", {}) or {}
    steps = [step("search", f"{sel.get('scenes_found', '?')} Sentinel-2 scenes in a "
                  f"{sel.get('lookback_days', '?')}-day lookback; {sel.get('scenes_probed', '?')} probed",
                  item("input", "scl", "Cloud is the Sentinel-2 SCL mask, a classification "
                       "with its own errors.", basis="stated_by_source")),
             step("select", f"scene {str(scene.get('datetime', '?'))[:10]}, "
                  f"{scene.get('days_before_start', '?')} days before the window; "
                  f"{'mosaic' if scene.get('is_mosaic') else 'single scene'}"
                  f"{', one datatake' if scene.get('single_datatake') else ''}",
                  item("tool", "valid_fraction", f"{_pct(scene.get('aoi_valid_fraction'))} of the "
                       f"land cloud-free (target {sel.get('threshold_requested', sel.get('min_valid_fraction', '?'))}); "
                       "the rest is not observed.", scene.get("aoi_valid_fraction"), "fraction of land"),
                  item("input", "scene_age", f"The scene is {scene.get('days_before_start', '?')} "
                       "days older than the event window.", scene.get("days_before_start"), "days"),
                  # Below the 0.7 target, the clearest scene is used (user
                  # decision 2026-11-11): the shortfall is this uncertainty.
                  item("input", "below_target",
                       f"No scene reached the {_pct((sel.get('best_available') or {}).get('target_valid_fraction'))} "
                       f"cloud-free target; the clearest scene was used, with "
                       f"{_pct((sel.get('best_available') or {}).get('valid_fraction'))} of the land observed, so "
                       f"{_pct((sel.get('best_available') or {}).get('unobserved_fraction'))} of the land is not "
                       "observed and every land-cover share describes the observed part only.",
                       (sel.get("best_available") or {}).get("unobserved_fraction"),
                       "fraction of land unobserved", "measured")
                  if sel.get("best_available") else
                  item("decision", "threshold_stepped_down",
                       f"No scene was clear enough at {sel.get('threshold_requested')}; the "
                       f"threshold stepped down ({', '.join(str(t) for t in sel.get('thresholds_tried') or [])}) "
                       f"and this scene was accepted at {sel.get('min_valid_fraction')}, so up to "
                       f"{_pct(1 - float(sel.get('min_valid_fraction') or 0))} of the land may be unobserved.",
                       sel.get("min_valid_fraction"), "threshold", "measured")
                  if sel.get("threshold_stepped_down") else None),
             step("indices", f"{', '.join(k.upper() for k in (p.get('indices') or {}))} over "
                  "cloud-free land",
                  item("tool", "saturation", f"{_pct(cov.get('saturated_fraction'))} of pixels saturated.",
                       cov.get("saturated_fraction"), "fraction")
                  if cov.get("saturated_fraction") is not None else None,
                  item("input", "observed_only", "Shares are of the observed land; the cloud-covered "
                       "part is not described.", basis="measured"))]
    return steps


def _terrain(p: dict, params: dict) -> list[dict]:
    src, classes = p.get("source") or {}, _g(p, "slope.classes", {}) or {}
    total = (classes.get("classified_pixels") or 0) + (classes.get("unclassified_pixels") or 0)
    return [step("tiles", f"{len(src.get('tiles') or [])} GLO-30 tile(s)"
                 f"{', mosaicked' if src.get('mosaicked') else ''}",
                 item("input", "surface_model", "GLO-30 is a surface model: canopy and buildings "
                      "add height, and so add slope.", 30, "m", "stated_by_source", "2011-2015")),
            step("slope", f"slope by {_g(p, 'slope.method', '?')}; sea excluded",
                 item("tool", "unclassified", f"{classes.get('unclassified_pixels', '?')} of {total} "
                      "pixels unclassified (no slope).", classes.get("unclassified_pixels"), "pixels"),
                 item("tool", "sea", f"{_g(p, 'aoi.land.excluded_km2', 0)} km2 excluded as sea.",
                      _g(p, "aoi.land.excluded_km2"), "km2"))]


def _water(p: dict, params: dict) -> list[dict]:
    th = p.get("thresholds") or {}
    return [step("occurrence", f"JRC GSW occurrence, permanent at >= {th.get('permanent')}%, "
                 f"seasonal at >= {th.get('seasonal')}%",
                 item("input", "vintage", "JRC Global Surface Water describes 1984-2021, "
                      "not the event year.", 2021, "year", "stated_by_source", "2021"),
                 item("tool", "unobserved", f"{_pct(_g(p, 'water.unobserved.land_fraction'))} of the "
                      "land has no observation.", _g(p, "water.unobserved.land_fraction"), "fraction"),
                 item("tool", "sea", f"{_g(p, 'aoi.land.excluded_km2', 0)} km2 excluded as sea.",
                      _g(p, "aoi.land.excluded_km2"), "km2"))]


def _availability(p: dict, params: dict) -> list[dict]:
    out = []
    for prod in p.get("products") or []:
        t = prod.get("totals") or {}
        out.append(step(f"search {prod.get('id')}",
                        f"{t.get('instances', 0)} pass(es) on {t.get('days_available', 0)} day(s)",
                        item("tool", "not_checkable", f"{prod.get('label')}: {t.get('days_not_checkable')} "
                             "day(s) with no data (neither the rolling archive nor GIBS holds them).",
                             t.get("days_not_checkable"), "days")
                        if t.get("days_not_checkable") else None,
                        item("input", "in_gibs", f"{prod.get('label')}: {t.get('days_in_gibs')} "
                             "day(s) read from GIBS imagery (the product's classes, "
                             "re-gridded by GIBS; one daily composite per day, 1 when it "
                             f"saw ground, 0 when only cloud: {t.get('days_all_cloud', 0)} "
                             "day(s); not a pass count).",
                             t.get("days_in_gibs"), "days", "stated_by_source")
                        if t.get("days_in_gibs") else None,
                        item("pipeline", "unreachable", f"{prod.get('label')}: the catalogue search failed.")
                        if prod.get("status") == "unreachable" else None))
    caveat = _g(p, "cloud.caveat")
    if caveat:
        out.append(step("overpass cloud", "model cloud at each nominal overpass",
                        item("input", "model_cloud", caveat, basis="stated_by_source")))
    return out


def _ranking(p: dict, params: dict) -> list[dict]:
    gate, method = p.get("gate") or {}, p.get("method") or {}
    top = min(p.get("ranking") or [], key=lambda r: r.get("rank", 99), default={})
    weights = [c.get("base_weight") for c in p.get("criteria") or [] if isinstance(c, dict)]
    equal = bool(weights) and len({round(w, 9) for w in weights if w is not None}) == 1
    return [step("indices", "five event indices read from the weather, land-cover and terrain payloads",
                 *[item("input", "index_caveat", c) for c in p.get("index_caveats") or []]),
            step("weights", f"{method.get('mode', '?')} mode: indices modulate criterion weights",
                 item("decision", "equal_weights", "Base weights are equal: a placeholder, not an "
                      "elicited weighting.", basis="assumed") if equal else None,
                 item("decision", "cloud_gate", f"The cloud gate sits at {gate.get('cloud_gate_pct')}% "
                      f"and {'can' if gate.get('can_fire') else 'cannot'} fire: high cloud penalises an "
                      "optical product, it does not remove it.", gate.get("cloud_gate_pct"), "%")),
            step("topsis", f"TOPSIS; top: {top.get('alternative', '?')} (closeness "
                 f"{round(top.get('closeness', 0), 4) if top else '?'})",
                 item("decision", "rank_not_usability", "A rank is not a usability claim; the cloud "
                      "index has to be read beside it.", basis="measured"),
                 item("decision", "wsm_missing", "Only TOPSIS is implemented; nothing here is "
                      "method-agnostic.", basis="measured") if not method.get("wsm_implemented") else None)]


def _sensitivity(p: dict, params: dict) -> list[dict]:
    mc = p.get("most_critical") or {}
    return [step("sweep", f"one-at-a-time weight sweep, {_g(p, 'method.search', 'bracketed and bisected')}",
                 item("decision", "weights_only", "Only weights are perturbed: scores, evidence and "
                      "parameters are held fixed.", basis="measured")),
            step("thresholds", f"most critical: {mc.get('criterion', '?')} {mc.get('name', '')}",
                 item("decision", "most_critical", f"{mc.get('name', mc.get('criterion', '?'))} changes the "
                      f"order after a weight change of {_num(mc.get('delta'))} "
                      f"({_pct(mc.get('relative_delta'))} of its weight).", mc.get("delta"), "weight")
                 if mc else None,
                 item("decision", "leader", f"{len(p.get('leader_changeable_by') or [])} criteria can "
                      "displace the top product.", len(p.get("leader_changeable_by") or []), "criteria"))]


def _where(p: dict, params: dict) -> list[dict]:
    counts = p.get("counts") or {}
    return [step("admin", f"{', '.join(_g(p, 'admin.second_order.units', []) or [])}",
                 item("input", "osm_mapped", "OpenStreetMap holds mapped features, not every feature.",
                      basis="stated_by_source")),
            step("counts", f"{len(counts.get('counts') or {})} categories in {counts.get('requests')} "
                 "requests",
                 item("pipeline", "missing_categories", f"Not measured (request failed): "
                      f"{', '.join(counts.get('missing'))}.", len(counts.get("missing")), "categories")
                 if counts.get("missing") else None),
            step("influences", f"water influences within {_g(p, 'influences.influence_km')} km",
                 item("tool", "not_upstream", "Searched by distance; nothing is claimed to be upstream.",
                      basis="measured"))]


def _depth(p: dict, params: dict) -> list[dict]:
    """calc_flood_depth reports its own stages and items; each item is put on
    the stage it belongs to, so Entro reads the depth error step by step."""
    own = p.get("uncertainty") or {}
    by_key = {i.get("key"): dict(i, level=lvl) for lvl in LEVELS for i in own.get(lvl) or []
              if isinstance(i, dict)}
    where = {"read the DEM": ("dem_surface_model", "dem_relative_le90", "dem_absolute_le90"),
             "extent onto the DEM grid": ("extent_resampled",),
             "boundary cells": ("boundary_cells", "boundary_definition", "edge_above_flood_ground_m",
                                "depth_bias"),
             "smooth the boundary": ("fwdet_parameters",),
             "water surface and depth": ("negative_depth",),
             "depth error": ("error_realisations", "error_model", "precision_not_accuracy"),
             "bands": ("vehicle_band_unstable", "advisory_band_unstable", "person_bands_pending")}
    return [step(s["step"], s["did"], *[by_key[k] for k in where.get(s["step"], ()) if k in by_key])
            for s in p.get("method_steps") or []]


def _generic(p: dict, params: dict) -> list[dict]:
    return []


ADAPTERS: dict[str, Callable[[dict, dict], list[dict]]] = {
    "fetch_weather": _weather, "fetch_lulc_data": _lulc, "fetch_terrain_data": _terrain,
    "fetch_permanent_water": _water, "data_availability": _availability,
    "rank_flood_products": _ranking, "sensitivity_analysis": _sensitivity,
    "where_am_i": _where, "calc_flood_depth": _depth,
}


def capture(tool: str, output: Any, params: dict | None = None) -> tuple[list[dict], dict]:
    """(steps, block) for one successful call.

    Steps come from the adapter; a tool with none gets one step built from its
    own uncertainty block, so every call has at least one. The block merges
    the tool's own items with the adapter's, level by level, and the tool's
    own always win: a level the tool filled is kept as it is.
    """
    if not isinstance(output, dict):
        return [], {lvl: [] for lvl in LEVELS}
    try:
        steps = ADAPTERS.get(tool, _generic)(output, params or {})
    except Exception as exc:                                   # noqa: BLE001
        # A payload shaped differently from the adapter's expectation costs
        # the steps, never the result -- and says so.
        steps = [step("steps not derived", f"{type(exc).__name__}: {exc}"[:160],
                      item("pipeline", "adapter_failed", "The steps could not be read from "
                           "this payload; its own uncertainty block still stands.",
                           basis="measured"))]
    own = output.get("uncertainty") if isinstance(output.get("uncertainty"), dict) else {}
    if not steps:
        flat = [dict(i, level=lvl) for lvl in LEVELS for i in own.get(lvl) or []
                if isinstance(i, dict)]
        steps = [step("run", f"{tool} returned its own uncertainty block", *flat)]
    block = {lvl: list(own.get(lvl) or []) for lvl in LEVELS}
    for lvl in LEVELS:
        if not block[lvl]:
            block[lvl] = [{k: v for k, v in i.items() if k != "level"}
                          for s in steps for i in s["uncertainty"] if i.get("level") == lvl]
    for n, s in enumerate(steps, start=1):
        s["n"] = n
    return steps, block


# -- the run register ------------------------------------------------------------
def record(tool: str, steps: list[dict], block: dict, run: dict) -> None:
    """Append one call to runs/<run_id>/steps.jsonl and uncertainty.json, and
    emit one `tool-step` event per step. Never fails the call."""
    from . import cache_policy
    from .logbook import event

    for s in steps:
        try:
            event("tool-step", src="monty", tool=tool, n=s.get("n"), step=s["step"],
                  did=s["did"][:160], items=len(s["uncertainty"]))
        except Exception:                                      # noqa: BLE001
            pass
    folder = cache_policy.run_dir()
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "steps.jsonl").open("a", encoding="utf-8") as fh:
            for s in steps:
                fh.write(json.dumps({"tool": tool, "run_id": run.get("run_id"), **s},
                                    default=str) + "\n")
        path = folder / "uncertainty.json"
        register = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        register.append({"tool": tool, "run": {k: run.get(k) for k in
                                               ("run_id", "cache", "started_at", "attempts",
                                                "duration_s", "source_failures")},
                         "steps": steps, "uncertainty": block})
        path.write_text(json.dumps(register, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


# -- the crisp view Entro reads ----------------------------------------------------
TAG = {"input": "IN", "tool": "TOOL", "pipeline": "RUN", "decision": "DEC"}


def crisp(entries: list[tuple[str, dict]], *, max_items: int = 6) -> str:
    """The register as short lines: per result, its steps; per step, its items.

        ## weather_grid (fetch_weather, cache on, run 2026...)
        1 request -- 56 points at 0.1 deg from ecmwf_ifs (9 km) ...
          IN   The weather is ecmwf_ifs: a model, not observations. [stated_by_source]

    `entries` are (stored name, payload). A payload stored before the step log
    existed has no `run.steps`; its block is printed as one step.
    """
    out: list[str] = []
    for name, payload in entries:
        if not isinstance(payload, dict):
            continue
        run = payload.get("run") or {}
        steps = run.get("steps")
        if not steps:
            steps, _ = capture(_tool_for(name), payload)
        out.append(f"## {name} (cache {run.get('cache', '?')}, run {run.get('run_id') or 'not recorded'})")
        out.extend(standing_lines(_tool_for(name)))
        for s in steps:
            out.append(f"{s.get('n', '-')} {s['step']} -- {s['did']}")
            for i in s["uncertainty"][:max_items]:
                out.append(f"  {TAG.get(i.get('level'), '?'):<4} {i['statement']} [{i.get('basis', '')}]")
            if len(s["uncertainty"]) > max_items:
                out.append(f"  ... {len(s['uncertainty']) - max_items} more")
        out.append("")
    return "\n".join(out).rstrip() or "No stored result carries a step log yet."


def standing_lines(tool: str) -> list[str]:
    """The tool's standing uncertainty as three crisp lines: data source,
    method, model. Read from the registry, so a statement edited there reaches
    every stored result's register at once. Not written yet is said."""
    from . import workflows

    u = workflows.tool_uncertainty(tool)
    return [f"  DATA   {u['data_source'] or 'not written yet for this tool'}",
            f"  METHOD {u['method'] or 'not written yet for this tool'}",
            f"  MODEL  {u['model']['status']}: {u['model']['how']}"]


def _tool_for(stored_name: str) -> str:
    from . import workflows

    tool = workflows.tool_of(stored_name)
    if tool != stored_name:
        return tool
    return {"mcdm": "rank_flood_products", "sensitivity": "sensitivity_analysis",
            "availability": "data_availability"}.get(stored_name, stored_name)


def table_rows(entries: list[tuple[str, dict]]) -> list[list[Any]]:
    """The register as table rows: result, step, level, statement, basis."""
    rows = []
    for name, payload in entries:
        if not isinstance(payload, dict):
            continue
        steps = (payload.get("run") or {}).get("steps") or capture(_tool_for(name), payload)[0]
        for s in steps:
            items = s["uncertainty"] or [{"level": "", "statement": "(no uncertainty recorded "
                                          "for this step)", "basis": ""}]
            for i in items:
                rows.append([name, f"{s.get('n', '-')}. {s['step']}", i.get("level", ""),
                             i["statement"], i.get("basis", "")])
    return rows
