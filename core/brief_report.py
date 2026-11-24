"""Rendering Norwood's pre-flood AOI brief into a readable thesis output.

The JSON Norwood emits is the record; this is the reading of it. Same split as
the weather output, and for the same reason: `inputs/` holds what a number can
be traced to, and the numbered markdown is what a person reads.

**Nothing here recomputes anything.** A field Norwood left null renders as
absent rather than being filled in from the Monty payloads. The whole point of
the contract is that Norwood decided what it could honestly say from
statistics alone; a renderer that quietly completed the gaps would be
overruling that decision with no record.
"""

from __future__ import annotations

from typing import Any


def _f(value: Any, places: int = 2) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.{places}f}"
    return str(value)


def _cell(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "--"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return _f(value, 4)
    return str(value) if value not in (None, "") else "--"


def _table(items: Any, columns: list[tuple[str, str]]) -> list[str]:
    """A markdown table from a list of dicts. Absent values become `--`."""
    if not items:
        return ["_None reported._", ""]
    out = ["| " + " | ".join(head for head, _ in columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append("| " + " | ".join(_cell(item.get(key))
                                     for _, key in columns) + " |")
    out.append("")
    return out


def brief_markdown(payload: dict[str, Any]) -> str:
    """Norwood's brief as a document, rendered from the JSON it emitted."""
    header = payload.get("report_header") or {}
    aoi = payload.get("aoi") or {}
    integrity = payload.get("input_integrity") or {}
    basis = payload.get("observation_basis") or {}
    comp = payload.get("surface_composition") or {}
    rel = payload.get("composition_reliability") or {}
    hydro = payload.get("hydrography") or {}
    terrain = payload.get("terrain_setting") or {}
    scape = payload.get("landscape_summary") or {}
    outlook = payload.get("flood_mapping_outlook") or {}

    out: list[str] = []
    add = out.append

    # -- what each figure means for the event (user 2026-11-02) ------------
    commentary = payload.get("event_commentary") or {}
    figures = [f for f in commentary.get("figures") or [] if f.get("points")]
    if figures:
        add("## What the figures mean for this event")
        add("")
        if commentary.get("event"):
            add(f"_{commentary['event']}_")
            add("")
        for fig in figures:
            add(f"**{str(fig.get('figure', 'figure')).replace('_', ' ')}**")
            add("")
            out.extend(f"- {pt}" for pt in fig["points"][:3])
            add("")

    # -- the one sentence that has to stand alone --------------------------
    add("## Verdict")
    add("")
    add(f"> {header.get('verdict') or '_none recorded_'}")
    add("")
    add(f"**Confidence: {header.get('confidence', '?')}** -- limited most by "
        f"{header.get('confidence_limiter') or 'nothing recorded'}.")
    add("")
    add(f"`{payload.get('schema', 'norwood/pre-flood-aoi-brief')}` "
        f"v{payload.get('version', '?')} | report "
        f"`{header.get('report_id', '?')}` | generated "
        f"{header.get('generated_at', '?')}")
    add("")
    add("**This is an areal report.** Every cross-layer statement compares how "
        "*much*, never *where*. The inputs are summary statistics, so two "
        "layers reporting the same water fraction is not evidence they see "
        "water in the same place -- `co_location_verified` is false throughout "
        "and cannot be otherwise from these inputs.")
    add("")

    add("## Inputs it rests on")
    add("")
    out.extend(_table(header.get("inputs"),
                      [("Payload", "name"), ("Cached", "cached_at"),
                       ("Summary", "summary")]))

    add("## AOI")
    add("")
    add(f"- Requested bbox: `{aoi.get('bbox_requested')}`")
    add(f"- Area: {_f(aoi.get('area_km2'), 1)} km2")
    if aoi.get("area_km2_by_source"):
        add(f"- By source: `{aoi['area_km2_by_source']}`")
    add(f"- Area disagreement: {_f(aoi.get('area_disagreement_pct'))} %")
    add("")

    # -- integrity first ---------------------------------------------------
    add("## Input integrity")
    add("")
    add("_Runs before any statistic below. A failure here invalidates the "
        "sections that follow rather than annotating them._")
    add("")
    foot = integrity.get("footprint") or {}
    out.extend(_table(foot.get("per_layer"),
                      [("Layer", "layer"), ("Overlap", "overlap_with_requested"),
                       ("Offset", "offset_deg"), ("Status", "status")]))
    add(f"- Minimum pairwise overlap: {_f(foot.get('min_pairwise_overlap'), 4)}")
    add(f"- Common extent: {_f(foot.get('common_extent_frac'), 4)}")
    add(f"- **Blocking: {foot.get('blocking')}**")
    add("")

    rec = integrity.get("recency") or {}
    add("### Recency")
    add("")
    add(f"- Scene: {rec.get('scene_datetime', '?')} -- "
        f"{rec.get('days_before_event_start', '?')} days before the window opens")
    add(f"- Baseline role: **{rec.get('baseline_role', '?')}**")
    if rec.get("caveat"):
        add(f"- {rec['caveat']}")
    add("")

    sel = integrity.get("scene_selection") or {}
    if sel:
        add("### Scene selection")
        add("")
        add(f"- Found {sel.get('scenes_found', '?')}, probed "
            f"{sel.get('scenes_probed', '?')}, rejected "
            f"{sel.get('scenes_rejected', '?')}")
        add(f"- Clear-scene rate: **{_f(sel.get('clear_scene_rate'), 3)}**")
        if sel.get("rejection_reasons"):
            add(f"- Rejection reasons: `{sel['rejection_reasons']}`")
        best = sel.get("best_rejected_alternative") or {}
        if best.get("scene_id"):
            add(f"- Best rejected alternative: `{best['scene_id']}` -- "
                f"{best.get('why_rejected', '')}")
        if sel.get("analyst_note"):
            add(f"- {sel['analyst_note']}")
        add("")

    # -- what the numbers describe ----------------------------------------
    add("## What the composition actually describes")
    add("")
    add(f"- Valid fraction: **{_f(basis.get('valid_fraction'), 4)}**")
    add(f"- Observed {_f(basis.get('observed_area_km2'), 1)} km2, unobserved "
        f"{_f(basis.get('unobserved_area_km2'), 1)} km2")
    if basis.get("unobserved_cause"):
        add(f"- Cause: `{basis['unobserved_cause']}`")
    if basis.get("directional_bias_warning"):
        add(f"- **{basis['directional_bias_warning']}**")
    policy = basis.get("masking_policy") or {}
    if policy:
        add(f"- Masked: {_cell(policy.get('masked_classes'))}; retained: "
            f"{_cell(policy.get('retained_classes'))}")
        if policy.get("asymmetry_flag"):
            add(f"- {policy['asymmetry_flag']}")
    add("")

    # -- composition -------------------------------------------------------
    add("## Surface composition")
    add("")
    add(f"**`is_land_cover: {comp.get('is_land_cover')}`** -- a spectral "
        "stratification, not a land-cover map.")
    add("")
    strat = comp.get("stratification") or {}
    if strat:
        add(f"Basis `{strat.get('basis', '?')}`, breaks "
            f"`{strat.get('class_breaks')}` "
            f"({strat.get('breaks_source', '?')}; documented by Monty: "
            f"{strat.get('breaks_documented_by_monty')}).")
        add("")
        out.extend(_table(strat.get("classes"),
                          [("Class", "label"),
                           ("Frac of observed", "fraction_of_observed"),
                           ("km2", "area_km2"),
                           ("Plausible cover", "plausible_land_cover"),
                           ("Separability", "separability")]))
        if strat.get("caveat"):
            add("Monty's own caveat, carried verbatim:")
            add("")
            add(f"> {strat['caveat']}")
            add("")

    if comp.get("index_summary"):
        add("### Index summary")
        add("")
        out.extend(_table(comp["index_summary"],
                          [("Index", "index"), ("mean", "mean"),
                           ("median", "median"), ("p10", "p10"),
                           ("p90", "p90"), ("std", "std"),
                           ("skew proxy", "skew_proxy")]))

    if comp.get("thematic_signals"):
        add("### Thematic signals")
        add("")
        add("_A single value is reported only where the proxies agree within a "
            "factor of two. Otherwise the range stands -- picking the "
            "convenient proxy is the failure that rule prevents._")
        add("")
        out.extend(_table(comp["thematic_signals"],
                          [("Theme", "theme"), ("Spread", "spread_ratio"),
                           ("Consensus", "consensus"),
                           ("Reported", "reported_value")]))

    # -- reliability -------------------------------------------------------
    het = rel.get("heterogeneity") or {}
    add("## Composition reliability")
    add("")
    add(f"- Entropy {_f(het.get('shannon_entropy_nats'), 3)} nats, evenness "
        f"{_f(het.get('evenness'), 3)} -- {het.get('interpretation', '?')}")
    shape = rel.get("distribution_shape") or {}
    if shape:
        add(f"- `{shape.get('index', '?')}` is **{shape.get('modality', '?')}**: "
            f"{shape.get('implication', '')}")
    if rel.get("proxy_agreement"):
        add("")
        out.extend(_table(rel["proxy_agreement"],
                          [("Theme", "theme"), ("Spread", "spread_ratio"),
                           ("Verdict", "verdict")]))
    add(f"- **Overall: {rel.get('overall', '?')}**")
    add("")

    # -- hydrography -------------------------------------------------------
    add("## Hydrography")
    add("")
    add("_The dry-water reference an analyst subtracts from a later flood "
        "extent._")
    add("")
    out.extend(_table(hydro.get("occurrence_classes"),
                      [("Class", "label"), ("Threshold %", "threshold_pct"),
                       ("km2", "area_km2"), ("Fraction", "fraction")]))
    if hydro.get("independent_water_estimates"):
        add("Independent estimates of the same quantity:")
        add("")
        out.extend(_table(hydro["independent_water_estimates"],
                          [("Method", "method"), ("km2", "area_km2"),
                           ("Extent", "extent")]))
        add(f"Spread ratio {_f(hydro.get('estimate_spread_ratio'))}, compared "
            f"**{hydro.get('comparison_basis', 'areal')}ly**. "
            f"`co_location_verified: {hydro.get('co_location_verified')}`.")
        add("")
    ref = hydro.get("recommended_reference") or {}
    if ref:
        add(f"**Recommended reference:** `{ref.get('method', '?')}` -- "
            f"{ref.get('rationale', '')}")
        add("")
    floor = hydro.get("look_alike_floor") or {}
    if floor:
        add(f"**Commission floor:** "
            f"{_f(floor.get('seasonal_plus_ephemeral_km2'))} km2 of seasonal "
            f"plus ephemeral water. {floor.get('implication', '')}")
        add("")

    # -- terrain -----------------------------------------------------------
    add("## Terrain")
    add("")
    add(f"- Model type: **{terrain.get('model_type', '?')}**")
    if terrain.get("dsm_caveat"):
        add(f"- {terrain['dsm_caveat']}")
    elev = terrain.get("elevation_m") or {}
    slope = terrain.get("slope_deg") or {}
    add(f"- Elevation {_f(elev.get('min'), 1)} to {_f(elev.get('max'), 1)} m, "
        f"median {_f(elev.get('median'), 1)}, relief "
        f"{_f(elev.get('relief_m'), 1)} m")
    low = elev.get("low_lying_fraction") or {}
    if low:
        add(f"- Low-lying ({low.get('rule', '?')}): {_f(low.get('value'), 3)} "
            f"= {_f(low.get('area_km2'), 1)} km2")
    add(f"- Slope mean {_f(slope.get('mean'))} deg, p90 {_f(slope.get('p90'))}, "
        f"frac < 5 deg {_f(slope.get('frac_lt_5'), 3)}, frac > 15 deg "
        f"{_f(slope.get('frac_gt_15'), 3)}")
    fp = terrain.get("floodplain_indication") or {}
    if fp:
        add(f"- Floodplain signal **{fp.get('low_gradient_low_lying_signal', '?')}** "
            f"-- {fp.get('caveat', '')}")
    add("")

    # -- the forward-looking half -----------------------------------------
    add("## The AOI as a place")
    add("")
    add(f"**{scape.get('archetype', '?')}** (confidence "
        f"{scape.get('archetype_confidence', '?')}). Dominant control: "
        f"{scape.get('dominant_control', '?')}.")
    add("")
    if scape.get("one_paragraph"):
        add(scape["one_paragraph"])
        add("")

    add("## Flood-mapping outlook")
    add("")
    if outlook.get("headline_for_analyst"):
        add(f"> {outlook['headline_for_analyst']}")
        add("")
    out.extend(_table(outlook.get("by_sensor_family"),
                      [("Family", "family"), ("Expected", "expected_performance"),
                       ("Dominant limitation", "dominant_limitation"),
                       ("Bias", "expected_bias"),
                       ("Area share", "affected_area_share"),
                       ("Conf.", "confidence")]))
    acq = outlook.get("acquisition_outlook") or {}
    if acq:
        add(f"- Optical clear-scene rate "
            f"{_f(acq.get('optical_clear_scene_rate'), 3)}: "
            f"{acq.get('implication', '')}")
    add(f"- Commission risk floor: "
        f"{_f(outlook.get('commission_risk_floor_km2'))} km2")
    add(f"- Omission risk share: {_f(outlook.get('omission_risk_share'), 3)}")
    add("")

    # -- findings ----------------------------------------------------------
    add("## Findings")
    add("")
    findings = sorted((payload.get("findings") or []),
                      key=lambda f: f.get("rank") if isinstance(f.get("rank"), int)
                      else 99)
    if not findings:
        add("_None reported._")
        add("")
    for finding in findings:
        add(f"**{finding.get('rank', '?')}. {finding.get('id', '?')}** "
            f"[{finding.get('severity', '?')} | confidence "
            f"{_f(finding.get('confidence'))} | "
            f"{finding.get('comparison_basis', '?')}]")
        add("")
        add(finding.get("statement", ""))
        add("")
        if finding.get("so_what"):
            add(f"*So what:* {finding['so_what']}")
            add("")

    add("## Cannot determine")
    add("")
    add("_A required section, not a fallback: what an analyst will reasonably "
        "ask that stats-only inputs cannot answer._")
    add("")
    out.extend(_table(payload.get("cannot_determine"),
                      [("Question", "question"), ("Reason", "reason"),
                       ("Would be resolved by", "would_be_resolved_by"),
                       ("Priority", "priority")]))

    add("## Analyst actions")
    add("")
    out.extend(_table(payload.get("analyst_actions"),
                      [("Action", "action"), ("Rationale", "rationale"),
                       ("Blocking", "blocking")]))

    add("## Data integrity")
    add("")
    out.extend(_table(payload.get("data_integrity"),
                      [("Code", "code"), ("Severity", "severity"),
                       ("Layer", "layer"), ("Detail", "detail")]))
    return "\n".join(out)
