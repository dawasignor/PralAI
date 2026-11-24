"""Choosing an NRT flood product for one event, by TOPSIS.

The static half -- which products exist, what they cost, and how badly each is
hurt by cloud, wind, canopy, terrain and buildings -- is in `_mcdm_data.py`
with its evidence. This module is the half that depends on the event.

**What the event actually changes is the WEIGHTS, not the scores.** A product
is no better at seeing through cloud on a clear day; what changes is how much
its cloud-blindness matters. So five indices read off the cached payloads --
cloud, wind, dense vegetation, steep ground, built-up -- scale the weight of
the criterion each one governs. That is `mode="WEIGHT"`, the default, and it
matches the framework's commitment that environmental factors are feasibility
gates and weight modulators.

`mode="MATRIX"` is the specification's literal alternative -- the index
multiplies the matrix cells instead -- and it is implemented because the
specification asked for it, not because it works. **Under vector normalisation
an index that multiplies every cell of a column by the same number cancels
out.** The workbook's own test cases TC5 and TC6 show dense vegetation at 40%
and 70% giving identical rankings. It is kept so the choice is visible and
refutable rather than quietly dropped.

**Only TOPSIS is implemented.** The specification also describes a weighted-sum
model as a robustness check and a Spearman comparison between the two; those
are not here yet, so nothing in this module's output may be described as
method-agnostic.

**The cloud gate is switched off** (user decision, 2026-09-22). It used to
remove optical products above 80% AOI cloud, on the reasoning that a sensor
which cannot see the ground is not a worse option but not an option. Both the
gate and the penalty's upper bound now sit at 100, so cloud penalises an
optical product's weight and never removes it.

The mechanism is intact rather than deleted: `cloud_gate` is still a parameter
and still reported, and setting it back below 100 restores the exclusion. What
changed is the value and, with it, the meaning of a high rank -- **eligibility
no longer carries information**, so a reader has to look at `W1` and the cloud
index to know whether a well-placed optical product could have seen anything.
Excluded alternatives, when there are any, are reported with the reason and
never silently dropped.

**The direction of each criterion** decides what counts as good. Every weather
and land-characteristic criterion is a cost; so are spatial resolution and revisit.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import workflow
from . import _mcdm_data as D

MODES = ("WEIGHT", "MATRIX")
# How the weights are built (user decision 2026-11-22): "event_first" raises
# each event criterion above its base by its index and shares what is left
# among the product criteria; "multiplicative" is the specification
# workbook's rule (base x modulator, renormalised), kept for its test cases.
WEIGHTINGS = ("event_first", "multiplicative")
WEIGHTING_RULE = (
    "Event criteria first: each starts at its base weight (1/12) and is raised by "
    "this event's index -- W1 by the cloud curve, W2 by the wind ramp, L1 by the "
    "dense-vegetation share, L2 by the steep share, L3 by the built-up share (52% "
    "dense vegetation makes L1 1/12 x 1.52); W3 is not raised. The weight left is "
    "divided equally among the six product criteria P1-P6.")


# ---------------------------------------------------------------------------
# The five event indices
# ---------------------------------------------------------------------------
def modulators(cloud_pct: float, wind_ms: float, veg_pct: float,
               steep_pct: float, urban_pct: float,
               params: dict[str, float] | None = None) -> dict[str, float]:
    """One multiplier per criterion, in [0, 1].

    `g(cloud)` is convex on purpose: the penalty is mild at moderate cloud and
    rises toward total cloud, so a 50%-cloudy event is not treated as half as
    bad as a 100%-cloudy one -- at k = 3 against a 100% bound it is about
    0.109. `f(wind)` is linear because no shape was specified and a straight
    line is the assumption that adds least.

    The terrain indices are shares, used directly: a criterion about canopy
    matters in proportion to how much canopy there is.
    """
    p = {**D.PARAMETERS, **(params or {})}

    low, high, k = p["cloud_lower"], p["cloud_upper"], p["cloud_k"]
    if cloud_pct <= low:
        g = 0.0
    elif cloud_pct >= high:
        g = 1.0
    else:
        g = ((math.exp(k * (cloud_pct - low) / (high - low)) - 1.0)
             / (math.exp(k) - 1.0))

    f = (wind_ms - p["wind_lower"]) / (p["wind_upper"] - p["wind_lower"])
    f = float(min(1.0, max(0.0, f)))

    by_index = {"cloud": g, "wind": f, "veg": veg_pct / 100.0,
                "steep": steep_pct / 100.0, "urban": urban_pct / 100.0}
    return {c["id"]: (1.0 if c["modulator"] is None
                      else by_index[c["modulator"]])
            for c in D.CRITERIA}


def _eligibility(cloud_pct: float, gate: float) -> dict[str, dict[str, Any]]:
    """Which alternatives survive the feasibility gate, and why not."""
    out = {}
    for alt in D.ALTERNATIVES:
        blocked = alt["sensor"] == "Optical" and cloud_pct > gate
        out[alt["id"]] = {
            "eligible": not blocked,
            "reason": (f"optical product removed: AOI cloud cover "
                       f"{cloud_pct:.4g}% exceeds the {gate:g}% feasibility "
                       f"gate. A sensor that cannot see the ground is not a "
                       f"worse option, it is not an option."
                       if blocked else None),
        }
    return out


# ---------------------------------------------------------------------------
# The payload -> index mapping (the workbook's "TO MAP" column)
# ---------------------------------------------------------------------------
#
# The specification names five indices and leaves the agent JSON keys blank,
# because it did not know them. This is that mapping, and it is the part with
# the judgement in it -- everything downstream is arithmetic.
#
# **Two of the five are shares of OBSERVED LAND, not of the AOI, and the
# specification asks for shares of the AOI.** Cloud removes a quarter of the
# Kelani basin from the Sentinel-2 scene, and it does not remove a random
# quarter: cloud and cirrus concentrate over high ground, where the canopy is
# denser. So the dense-vegetation share is measured over ground that is
# systematically less forested than the part behind the cloud, and the
# built-up share over ground that is systematically lower and more built. The
# direction of both biases is known; the size is not.
#
# The alternative would be to scale the observed share up to the AOI, which
# assumes the unobserved part resembles the observed part -- an assumption the
# cloud distribution is known to violate. So the observed share is used and
# labelled, rather than corrected by an assumption that is false in a
# direction we can name.


def _mean(values) -> float:
    values = [float(v) for v in values if v is not None]
    if not values:
        raise ValueError("no values to average")
    return sum(values) / len(values)


# Which settings make a Sentinel-2 run at least as permissive as the defaults:
# "le" -- a value at or below the default lets more scenes through; "ge" -- at
# or above. A run that failed under settings this permissive would also have
# failed at the defaults, so its failure is evidence for the fallback. A run
# that failed under STRICTER settings is not: the defaults might have passed.
#
# min_valid_fraction, step_down_to and lookback_days are not here:
# fetch_lulc_data runs at 0.7, with no step-down, over 90 days, whatever it is
# sent (user decisions 2026-11-01), so a value in the recorded params is what
# was asked for, not what ran.
_PERMISSIVE = {"min_aoi_coverage": "le",
               "max_scene_cloud": "ge",
               "max_probes": "ge", "probe_decimation": "le"}


def s2_failed_at_defaults(failure: dict | None,
                          bbox: list[float] | None = None) -> tuple[bool, str]:
    """(ok, reason): is this recorded failure evidence that Sentinel-2 cannot
    reach its valid-fraction threshold for this AOI at the defaults -- 0.7,
    fixed, with no step-down (user decision 2026-11-01)?

    The WorldCover fallback rests on this answer. It is
    yes for a recorded `BelowValidFraction` -- the cloud failure, typed in
    sentinel.py -- and for `NoUsableMosaic`, the tiling failure that remains
    after tiles from a second UTM zone were dropped (user decision
    2026-10-27), under settings no stricter than the defaults, over the same
    bounding box. Any other failure (network, an untyped ValueError), a
    failure under a raised threshold, or no record at all is no.
    """
    import inspect

    from .sentinel import fetch_lulc_data

    if not failure:
        return False, "no recorded Sentinel-2 failure for this AOI"
    error = failure.get("error") or {}
    if error.get("exception") not in ("BelowValidFraction", "NoUsableMosaic"):
        return False, (f"the recorded Sentinel-2 failure is "
                       f"{error.get('exception') or error.get('error_type') or 'unknown'}"
                       f", not the cloud threshold or a tiling failure")
    params = failure.get("params") or {}
    defaults = {k: v.default for k, v in
                inspect.signature(fetch_lulc_data).parameters.items()}
    checks = dict(_PERMISSIVE)
    if "are fixed" not in str(error.get("message", "")):
        # Recorded before the threshold and the lookback were both fixed
        # (the message then says "... are fixed"): what was
        # sent is what ran, so they are checked like the rest (the Bangkok
        # lulc failure of 2026-11-01 ran over 30 days, and is not evidence).
        checks.update({"min_valid_fraction": "le", "lookback_days": "ge"})
    for key, direction in checks.items():
        if key not in params:
            continue
        got, default = float(params[key]), float(defaults[key])
        if (direction == "le" and got > default) or (direction == "ge" and got < default):
            return False, (f"the recorded failure used {key}={params[key]}, "
                           f"stricter than the default {defaults[key]}")
    if params.get("mosaic") is False and defaults.get("mosaic") is True:
        return False, "the recorded failure ran with mosaic=False"
    if bbox is not None and params.get("bbox") is not None:
        if [round(float(v), 6) for v in params["bbox"]] != \
                [round(float(v), 6) for v in bbox]:
            return False, "the recorded failure is for a different bounding box"
    return True, error.get("message", "Sentinel-2 failed its threshold")


def indices_from_payloads(weather: dict, lulc: dict | None, terrain: dict, *,
                          availability: dict | None = None,
                          worldcover: dict | None = None,
                          lulc_failure: dict | None = None,
                          bui_threshold: float | None = None) -> dict[str, Any]:
    """The five event indices, with where each one came from and what it is of.

    Returns `{"indices", "index_source", "provenance", "caveats",
    "uncertainty"}`. The provenance entries are not decoration: an index is a
    number that moves a ranking, and a reader has to be able to see which
    field it was read from and which denominator it is a share of.

    **Land cover is a hybrid**. Sentinel-2 (`lulc`) is
    used whenever it is there. Without it, the WorldCover payload is used for
    the vegetation and built-up shares -- but only when `lulc_failure` shows
    Sentinel-2 failed its 0.7 threshold at the defaults
    (`s2_failed_at_defaults`). Sentinel-2 merely not run is not a reason to
    fall back, and raises. The threshold itself is never touched.

    **Every sentence is built from the payloads** (D-32).
    Nothing here describes a place, a duration or a land-cover character that
    the payloads do not state. Where a claim would need a measurement the
    payloads do not carry -- where the cloud sat relative to the terrain, say
    -- the sentence says it is not measured instead of asserting a direction.
    """
    threshold = (D.PARAMETERS["bui_threshold"] if bui_threshold is None
                 else float(bui_threshold))
    provenance: list[dict[str, Any]] = []
    caveats: list[str] = []
    decision: list[dict[str, Any]] = []

    # --- cloud and wind: AT THE ACQUISITIONS, in local time -----------------
    # User decision 2026-11-22: cloud at the optical passes, wind at the radar
    # passes, from Module 1's pass times and Module 2's hourly series, every
    # time converted to local first (core/pass_weather.py). No window mean:
    # without the passes the two indices are not known.
    from ..pass_weather import RULE as PASS_RULE, pass_conditions

    if not availability:
        raise ValueError(
            "cloud and wind are read at the acquisition times listed by Module 1 "
            "(data_availability), which is not stored; run it first. The window "
            "mean is no longer used.")
    at_passes = pass_conditions(weather, availability)
    caveats.extend(at_passes["notes"])
    gust = (weather.get("event_summary") or {}).get("max_gust_ms")
    for key, idx, what in (("cloud_pct", at_passes["cloud"], "optical"),
                           ("wind_ms", at_passes["wind"], "radar")):
        if idx["withheld"]:
            caveats.append(
                f"{idx['withheld']} of the {idx['passes'] + idx['withheld']} {what} passes "
                f"fall on hours with no weather data; {key} is the mean over the "
                f"{idx['passes']} with data, the others excluded, not filled.")
        provenance.append({
            "index": key, "value": round(idx["value"], 4),
            "from": idx["from"],
            "reduction": (f"mean over the {idx['passes']} {what} pass(es) in the window, "
                          f"each interpolated between the model hours either side; local "
                          f"time ({at_passes['timezone']})"),
            "of": "the AOI bounding box, grid-mean per hour",
            "passes": idx["list"],
            "note": (f"{PASS_RULE}. Products: {', '.join(idx['products'])}."
                     + ("" if key == "cloud_pct" else
                        f" Sustained 10 m wind, not gusts (the event's maximum gust was "
                        f"{gust} m/s)."))})
    cloud_pct, wind_ms = at_passes["cloud"]["value"], at_passes["wind"]["value"]

    # --- dense vegetation and built-up: Sentinel-2, or the WorldCover fallback
    if lulc is not None:
        veg_pct, urban_pct, land_source = _s2_land(
            lulc, threshold, provenance, caveats)
    elif worldcover is not None:
        bbox = (worldcover.get("aoi") or {}).get("bbox")
        ok, why = s2_failed_at_defaults(lulc_failure, bbox)
        if not ok:
            raise ValueError(
                f"the WorldCover fallback needs Sentinel-2 to have failed its "
                f"threshold at the defaults for this AOI, and {why}. Run "
                f"fetch_lulc_data first; the threshold is not lowered.")
        veg_pct, urban_pct, land_source = _worldcover_land(
            worldcover, lulc_failure, why, provenance, caveats, decision)
    else:
        raise ValueError("no land cover: neither the Sentinel-2 payload nor "
                         "the WorldCover fallback is available")

    # --- steep ground ------------------------------------------------------
    from .terrain import STEEP_BANDS_19_21_NOV, STEEP_CLASSES, STEEP_FROM_DEG

    slope = terrain["slope"]["classes"]["fractions"]
    # Slope >= 15 degrees: the steep and very_steep classes (restored
    # 2026-11-21). A result stored on 19-21 Nov carries 5-degree bands; the
    # bands from 15 up are the same share.
    names = ([n for n in STEEP_CLASSES if n in slope]
             or [n for n in STEEP_BANDS_19_21_NOV if n in slope])
    if not names:
        raise ValueError("the terrain payload has no slope classes from 15 degrees up")
    steep_pct = sum(float(slope[n]) for n in names) * 100.0
    provenance.append({
        "index": "steep_pct", "value": round(steep_pct, 4),
        "from": "terrain.slope.classes.fractions." + " + .".join(names),
        "reduction": f"share of land with slope >= {STEEP_FROM_DEG:g} degrees, as a percentage",
        "of": "the whole land area -- the DEM carries no cloud mask, so this "
              "one really is an AOI-wide share",
        "note": f"Copernicus GLO-30 is a SURFACE model: canopy and building "
                f"heights inflate slope over vegetated and built ground, so "
                f"the steep share is an overestimate wherever the canopy is "
                f"tall. Here {veg_pct:.1f}% of the land is dense vegetation "
                f"({land_source}) and {urban_pct:.1f}% built-up, which is how "
                f"much ground that bias can reach."})

    return {
        "indices": {
            "cloud_pct": round(cloud_pct, 6),
            "wind_ms": round(wind_ms, 6),
            "veg_pct": round(veg_pct, 6),
            "steep_pct": round(steep_pct, 6),
            "urban_pct": round(urban_pct, 6),
        },
        # Which tool each index came from. The fact sheet and every report
        # say this beside the number, because a fallback that is not named is
        # a substitution the reader cannot see.
        "index_source": {"cloud_pct": "fetch_weather at data_availability passes",
                         "wind_ms": "fetch_weather at data_availability passes",
                         "veg_pct": land_source, "steep_pct": "fetch_terrain_data",
                         "urban_pct": land_source},
        "provenance": provenance,
        "caveats": caveats,
        "uncertainty": {"decision": decision},
    }


def _s2_land(lulc: dict, threshold: float, provenance: list, caveats: list
             ) -> tuple[float, float, str]:
    """veg_pct and urban_pct from the Sentinel-2 payload (unchanged numbers)."""
    from .sentinel import DENSE_CLASS, DENSE_CLASS_BEFORE

    classes = lulc["indices"]["ndvi"]["classes"]
    # NDVI >= 0.60: "very_high_greenness" since 2026-11-19 (Aryal et al. 2022
    # breaks), "dense_vegetation" in results stored before -- the same break.
    dense = DENSE_CLASS if DENSE_CLASS in classes["fractions"] else DENSE_CLASS_BEFORE
    veg_pct = float(classes["fractions"][dense]) * 100.0
    valid = (lulc["indices"]["ndvi"]["stats"]["coverage"]["valid_fraction"])
    provenance.append({
        "index": "veg_pct", "value": round(veg_pct, 4),
        "from": f"lulc.indices.ndvi.classes.fractions.{dense} (NDVI >= 0.60)",
        "reduction": "the NDVI greenness class share, as a percentage",
        "of": f"OBSERVED land ({valid:.4f} of the land was cloud-free), "
              f"not the whole AOI",
        "index_source": "fetch_lulc_data",
        "note": "These are NDVI greenness bins, not a land-cover "
                "classification: a single date cannot separate forest from "
                "shrub from cropland, and in the growing season a closed "
                "canopy and a dense crop both sit near 0.8. The criterion "
                "asks for dense vegetation as a proxy for canopy blocking, "
                "which is what this measures."})
    # The direction of the cloud bias would need to know where the cloud sat
    # relative to the terrain and the canopy. The payload does not say, so
    # the caveat states the unobserved share and does not guess a direction.
    caveats.append(
        f"veg_pct is a share of the OBSERVED land, not of the AOI: "
        f"{(1 - valid) * 100:.1f}% of the land was cloud-covered in the "
        f"Sentinel-2 scene, and the unobserved part may differ from the "
        f"observed part in either direction. Where the cloud sat relative to "
        f"the terrain and the canopy is not measured here.")

    histogram = lulc["indices"]["bui"]["stats"]["histogram"]
    edges, counts = histogram["bin_edges"], histogram["counts"]
    total = sum(counts)
    if total <= 0:
        raise ValueError("the BUI histogram is empty; urban_pct cannot be "
                         "derived and must not be guessed")

    exact = any(abs(e - threshold) < 1e-12 for e in edges)
    if exact:
        start = next(i for i, e in enumerate(edges)
                     if abs(e - threshold) < 1e-12)
        above = sum(counts[start:])
        method = (f"exact: the threshold {threshold:g} is a histogram bin "
                  f"edge, so the bins above it sum without interpolation")
    else:
        # Linear interpolation inside the straddling bin: the least-committal
        # assumption available, and it is stated rather than hidden.
        above = 0
        for i, count in enumerate(counts):
            lo, hi = edges[i], edges[i + 1]
            if lo >= threshold:
                above += count
            elif hi > threshold:
                above += count * (hi - threshold) / (hi - lo)
        method = (f"interpolated: {threshold:g} falls inside a bin, and the "
                  f"count is split linearly across it -- an assumption about "
                  f"a distribution we cannot see")
        caveats.append(
            f"urban_pct was interpolated inside a histogram bin because the "
            f"BUI threshold {threshold:g} is not a bin edge. The workflow "
            f"returns no BUI class fractions, so the share cannot be counted "
            f"exactly from this payload.")

    urban_pct = above / total * 100.0
    provenance.append({
        "index": "urban_pct", "value": round(urban_pct, 4),
        "from": f"lulc.indices.bui.stats.histogram, counts above "
                f"BUI > {threshold:g}",
        "reduction": method,
        "of": "OBSERVED land -- the BUI histogram counts cloud-free pixels",
        "index_source": "fetch_lulc_data",
        "note": "BUI cannot separate built-up from bare soil in arid terrain "
                "and is unvalidated over bare ground. The index is a proxy "
                "for built-up, not a measurement of it."})
    caveats.append(
        f"urban_pct is a share of the OBSERVED land, like veg_pct: the "
        f"{(1 - valid) * 100:.1f}% under cloud may be more or less built-up "
        f"than the observed part, and this payload cannot say which.")
    return veg_pct, urban_pct, "fetch_lulc_data"


def _worldcover_land(wc: dict, failure: dict, why: str, provenance: list,
                     caveats: list, decision: list) -> tuple[float, float, str]:
    """veg_pct and urban_pct from WorldCover, when Sentinel-2 failed (D-8)."""
    fractions = wc["classes"]["fractions"]
    valid = float(wc["coverage"]["valid_fraction"])
    year = (wc.get("source") or {}).get("time_start", "")[:4] or "2021"
    acc = (wc.get("accuracy") or {}).get("per_class") or {}
    tree, built = acc.get("tree_cover") or {}, acc.get("built_up") or {}
    veg_pct = float(fractions["tree_cover"]) * 100.0
    urban_pct = float(fractions["built_up"]) * 100.0
    best = ((failure.get("error") or {}).get("message") or why)

    provenance.append({
        "index": "veg_pct", "value": round(veg_pct, 4),
        "from": "worldcover.classes.fractions.tree_cover (class 10)",
        "reduction": "the WorldCover tree-cover class share, as a percentage",
        "of": f"classified land ({valid:.4f} of the land has a class)",
        "index_source": "fetch_worldcover",
        "note": f"FALLBACK: Sentinel-2 failed its threshold. Tree cover is "
                f"not dense NDVI: it is a classified {year} land cover, not a "
                f"greenness measurement of the weeks before the event, and a "
                f"dense crop counts here only if WorldCover called it tree "
                f"cover. Published accuracy for the class: user's "
                f"{tree.get('users_pct')}%, producer's "
                f"{tree.get('producers_pct')}% (global)."})
    provenance.append({
        "index": "urban_pct", "value": round(urban_pct, 4),
        "from": "worldcover.classes.fractions.built_up (class 50)",
        "reduction": "the WorldCover built-up class share, as a percentage",
        "of": f"classified land ({valid:.4f} of the land has a class)",
        "index_source": "fetch_worldcover",
        "note": f"FALLBACK: Sentinel-2 failed its threshold. Built-up is not "
                f"BUI > 0.2: it is a classified {year} land cover, so it "
                f"neither confuses bare soil with buildings the way BUI can "
                f"nor sees anything built since {year}. Published accuracy "
                f"for the class: user's {built.get('users_pct')}%, producer's "
                f"{built.get('producers_pct')}% (global)."})
    caveats.append(
        f"veg_pct and urban_pct come from ESA WorldCover {year}, not from "
        f"Sentinel-2 before the event: Sentinel-2 failed its 0.7 threshold "
        f"({best[:200]}). WorldCover is a {year} snapshot, so it does not "
        f"address how the land looked in the weeks before the event.")
    decision.append({
        "key": "land_cover_fallback",
        "statement": f"Sentinel-2 failed its 0.7 valid-fraction threshold at "
                     f"the defaults, so the vegetation and built-up indices "
                     f"use WorldCover {year} (tree cover for dense vegetation, "
                     f"built-up for BUI > 0.2). The threshold was not "
                     f"lowered. The definitions differ, and the snapshot "
                     f"predates the event.",
        "value": "fetch_worldcover", "unit": "", "basis": "measured",
        "vintage": year, "vault_note": ""})
    return veg_pct, urban_pct, "fetch_worldcover"


# ---------------------------------------------------------------------------
# TOPSIS
# ---------------------------------------------------------------------------
def topsis(matrix: np.ndarray, weights: np.ndarray,
           directions: np.ndarray) -> dict[str, Any]:
    """Closeness to the ideal, for the eligible alternatives only.

    Vector normalisation, then weighting, then distance to the best and worst
    synthetic alternatives the candidate set can offer.

    **The ideal is not an absolute.** PIS and NIS are built column-wise from
    the candidates that are actually present, so removing an alternative moves
    them. That is why the excluded products are reported: a closeness of 0.88
    among two survivors is not the same claim as 0.88 among five.

    A column whose norm is zero -- every candidate identical on that criterion
    -- normalises to zero rather than dividing by zero. It then contributes
    nothing to any distance, which is correct: a criterion that does not
    discriminate should not move the ranking.

    **Everything is coerced to float on the way in**, and it has to be. The
    raw matrix is integer-valued -- 250 m posting, 12 days, a 23-year archive,
    an ordinal 0-2 -- while normalisation divides those into the 0-1 range
    that the rest of the method works in. `np.zeros_like(matrix)` inherits the
    input's dtype, so an integer matrix made `np.divide` raise rather than
    normalise, and every caller had to remember `dtype=float`. Coercing here
    makes that structural: the MCDM run, the weight sensitivity and anything
    built on them all reach this one function.
    """
    matrix = np.asarray(matrix, dtype=float)
    weights = np.asarray(weights, dtype=float)
    directions = np.asarray(directions, dtype=int)

    norms = np.sqrt((matrix ** 2).sum(axis=0))
    normalised = np.divide(matrix, norms, out=np.zeros_like(matrix),
                           where=norms > 0)
    weighted = normalised * weights

    pis = np.where(directions == 1, weighted.max(axis=0), weighted.min(axis=0))
    nis = np.where(directions == 1, weighted.min(axis=0), weighted.max(axis=0))

    d_plus = np.sqrt(((weighted - pis) ** 2).sum(axis=1))
    d_minus = np.sqrt(((weighted - nis) ** 2).sum(axis=1))
    total = d_plus + d_minus
    closeness = np.divide(d_minus, total, out=np.zeros_like(total),
                          where=total > 0)

    # Competition ranking: ties share the better rank, and the next rank skips.
    ranks = 1 + np.array([(closeness > value).sum() for value in closeness])

    return {
        "normalised": normalised,
        "weighted": weighted,
        "pis": pis,
        "nis": nis,
        "d_plus": d_plus,
        "d_minus": d_minus,
        "closeness": closeness,
        "ranks": ranks,
    }


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------
@workflow(
    "rank_flood_products",
    "Rank the five NRT flood-mapping products for one event by TOPSIS, from "
    "the AOI's cloud cover, wind speed, dense-vegetation share, steep-terrain "
    "share and built-up share. High cloud penalises an optical product's "
    "weight rather than removing it; the feasibility gate sits at 100% and "
    "cannot fire unless it is lowered. Returns the decision matrix with its "
    "evidence, the normalised and "
    "weighted matrices, the ideal and anti-ideal points, and the ranking. "
    "`weights` (e.g. {\"L1\": 0.2}) and `scores` (e.g. {\"P2\": {\"gfm\": 6}}) "
    "replace a weight or a score the user named; with neither, the weights and "
    "scores the user set at Module 6 (mcdm_decision_matrix) are used.",
    label="NRT flood product ranking",
    source="MCDM template v3",
    data_source_uncertainty=(
        "What: 30 evidence scores, five contested and some resting on author rules "
        "with no document behind them; the SAR revisit score (P2) is unresolved, "
        "and the five event values carry the uncertainty of weather, land cover and "
        "terrain. Affects: the product order itself. Reduce: settle the contested "
        "scores and P2 with a second coder; source the author rules."),
    method_uncertainty=(
        "What: TOPSIS only, with no weighted-sum check or Spearman comparison; the "
        "equal 1/12 weights are a placeholder, and with the cloud gate off a rank "
        "is not a usability claim. Affects: the order can depend on the weighting "
        "more than on the evidence. Reduce: decide AHP or entropy weights, add the "
        "weighted-sum check, and read the rank with sensitivity_analysis and the "
        "cloud value beside it."),
)
def rank_flood_products(
    cloud_pct: float,
    wind_ms: float,
    veg_pct: float,
    steep_pct: float,
    urban_pct: float,
    mode: str = "WEIGHT",
    base_weights: dict[str, float] | list[float] | None = None,
    parameters: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
    output_dir: str = "",
    event_id: str = "",
    scores: dict[str, Any] | None = None,
    use_module6: bool = True,
    weighting: str = "event_first",
) -> dict[str, Any]:
    """Rank the candidate flood products for one event.

    Args:
        cloud_pct: AOI cloud cover for the event, 0-100.
        wind_ms: AOI wind speed for the event, m/s, >= 0.
        veg_pct: share of the AOI in the dense-vegetation class, 0-100.
        steep_pct: share classified steep or very steep, 0-100.
        urban_pct: share with BUI above the built-up threshold, 0-100.
        mode: "WEIGHT" (default) scales criterion weights by the indices;
            "MATRIX" multiplies the matrix cells instead and is kept only
            because the specification describes it -- see the module docstring
            for why it cancels.
        base_weights: per-criterion weights before modulation, by criterion id
            or as a list in criterion order. Defaults to equal (1/12), which
            is a placeholder: AHP and entropy weighting are not decided.
        parameters: overrides for the author rules (gate, bounds, curve shape).
        weights: the user's own FINAL weights by criterion id (Module 7, user
            decision 2026-11-19: "allow adjusting" the weights Module 6
            showed). Criteria not named keep their computed weight; the set
            is renormalised to sum to one, and every change is recorded in
            `weights_changed` so the output says what the user moved.
        scores: the user's own SCORES (user decision 2026-11-22), as
            {"P2": {"gfm": 6}} or {"P2|gfm": 6}; product by id or name. Each
            is checked against its criterion's scale, replaces that one cell,
            and is listed in `scores_changed` with the value it replaced.
        weighting: "event_first" (default, user decision 2026-11-22) or
            "multiplicative" (the specification workbook's rule); see
            WEIGHTING_RULE.
        use_module6: with neither `weights` nor `scores` given, use the ones
            the user set at Module 6 (the stored mcdm_decision_matrix), so the
            ranking ranks what Module 6 showed. Library callers (the
            sensitivity sweep) pass False.
        output_dir, event_id: where the closeness chart is drawn
            (`<output_dir>/<event_id>/topsis_closeness.png`). Empty: no chart
            -- library callers (the sensitivity sweep, scripts) draw nothing.

    Returns the decision matrix with per-cell evidence, the weights before and
    after modulation, the normalised and weighted matrices, the ideal and
    anti-ideal points, the ranking, and every alternative excluded by the gate
    with its reason.
    """
    mode = str(mode).upper()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    named = {"cloud_pct": cloud_pct, "wind_ms": wind_ms, "veg_pct": veg_pct,
             "steep_pct": steep_pct, "urban_pct": urban_pct}
    for key, value in named.items():
        if value is None:
            raise ValueError(
                f"{key} is missing. It is not imputed: an index the pipeline "
                "could not produce is reported to the analyst, because a "
                "guessed index changes the ranking invisibly.")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{key} must be a number, got {value!r}")
    for key in ("cloud_pct", "veg_pct", "steep_pct", "urban_pct"):
        if not 0.0 <= float(named[key]) <= 100.0:
            raise ValueError(f"{key} must be a percentage in 0-100, "
                             f"got {named[key]}")
    if wind_ms < 0:
        raise ValueError(f"wind_ms must be >= 0, got {wind_ms}")

    params = {**D.PARAMETERS, **(parameters or {})}
    criteria = list(D.CRITERION_IDS)
    stated_at = "Module 7"
    if use_module6 and weights is None and scores is None:
        kept = _module6_choices()
        if kept:
            weights, scores = kept.get("weights"), kept.get("scores")
            stated_at = "Module 6"
    score_changes = _score_overrides(scores)
    user_weights = dict(weights) if isinstance(weights, dict) else None
    if weights is not None and user_weights is None:
        raise ValueError("weights must be a mapping of criterion id to weight, e.g. "
                         "{\"W1\": 0.2}")

    # --- weights -----------------------------------------------------------
    if base_weights is None:
        base = {c: 1.0 / len(criteria) for c in criteria}
    elif isinstance(base_weights, dict):
        unknown = [k for k in base_weights if k not in criteria]
        if unknown:
            raise ValueError(f"base_weights names unknown criteria: {unknown}")
        base = {c: float(base_weights.get(c, 0.0)) for c in criteria}
    else:
        if len(base_weights) != len(criteria):
            raise ValueError(f"base_weights needs {len(criteria)} values in "
                             f"criterion order, got {len(base_weights)}")
        base = dict(zip(criteria, (float(v) for v in base_weights)))

    mods = modulators(cloud_pct, wind_ms, veg_pct, steep_pct, urban_pct, params)
    if weighting not in WEIGHTINGS:
        raise ValueError(f"weighting must be one of {WEIGHTINGS}, got {weighting!r}")
    event_ids = [c["id"] for c in D.CRITERIA if c["group"] != "Product"]
    product_ids = [c["id"] for c in D.CRITERIA if c["group"] == "Product"]
    # How much this event RAISES each event criterion above its base: the
    # index itself (0-1). W3 has none -- darkness comes every night -- so it
    # stays at its base.
    increase = {c["id"]: (0.0 if c["modulator"] is None else mods[c["id"]])
                for c in D.CRITERIA if c["group"] != "Product"}

    if mode == "MATRIX":
        effective = dict(base)
    elif weighting == "event_first":
        # User decision 2026-11-22. The event criteria are allocated first:
        # each is its base weight raised by its index (52% dense vegetation
        # makes L1 1/12 x 1.52). What is left is shared among the product
        # criteria in proportion to their base weights -- equally, at the
        # default. Before this the event could only LOWER a weight, so no
        # event criterion could ever outweigh a product criterion.
        effective = {c: base[c] * (1.0 + increase[c]) for c in event_ids}
        left = 1.0 - sum(effective.values())
        product_base = sum(base[c] for c in product_ids)
        if left < -1e-12:
            raise ValueError(
                f"the event criteria take {1 - left:.4f} of the weight, more than "
                "the whole; lower their base weights.")
        left = max(left, 0.0)
        for c in product_ids:
            effective[c] = (left * base[c] / product_base if product_base > 0
                            else 0.0)
    else:
        # The specification workbook's rule (v3): every criterion is its base
        # weight times its modulator, then all are renormalised. Kept so the
        # workbook's test cases still reproduce.
        effective = {c: base[c] * mods[c] for c in criteria}

    total_weight = sum(effective.values())
    if total_weight <= 0:
        raise ValueError(
            "every criterion weight is zero, so nothing can be ranked. In "
            "WEIGHT mode this happens when every index is zero -- a benign "
            "event with no vegetation, terrain or built-up share -- and the "
            "answer is that the product criteria should carry the decision, "
            "not that the ranking is undefined.")
    weights = {c: effective[c] / total_weight for c in criteria}

    # --- the user's own weights (Module 7) ------------------------------------
    computed = dict(weights)
    changed: list[dict[str, Any]] = []
    if user_weights:
        unknown = [k for k in user_weights if k not in criteria]
        if unknown:
            raise ValueError(f"weights names unknown criteria: {unknown}; known: {criteria}")
        bad = {k: v for k, v in user_weights.items()
               if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0}
        if bad:
            raise ValueError(f"weights must be non-negative numbers, got {bad}")
        stated = {c: float(user_weights.get(c, computed[c])) for c in criteria}
        total = sum(stated.values())
        if total <= 0:
            raise ValueError("the stated weights sum to zero; nothing can be ranked")
        weights = {c: stated[c] / total for c in criteria}
        changed = [{"id": c, "computed": round(computed[c], 6), "used": round(weights[c], 6),
                    "stated": user_weights.get(c)}
                   for c in criteria if abs(weights[c] - computed[c]) > 1e-9]

    # --- the matrix --------------------------------------------------------
    gate = params["cloud_gate"]
    eligibility = _eligibility(cloud_pct, gate)
    eligible_ids = [a for a in D.ALTERNATIVE_IDS if eligibility[a]["eligible"]]
    if len(eligible_ids) < 2:
        raise ValueError(
            f"only {len(eligible_ids)} alternative(s) survive the cloud gate "
            f"at {cloud_pct:.4g}% cloud; a ranking needs at least two. That "
            "is a finding about the event, not a failure to work around.")

    applied = {alt: dict(D.STATIC_MATRIX[alt]) for alt in D.ALTERNATIVE_IDS}
    scores_changed = []
    for (crit, alt), value in score_changes.items():
        scores_changed.append({"criterion": crit, "alternative": alt,
                               "was": applied[alt][crit], "now": value})
        applied[alt][crit] = value
    if mode == "MATRIX":
        for alt in applied:
            for c in criteria:
                applied[alt][c] *= mods[c]

    matrix = np.array([[applied[a][c] for c in criteria] for a in eligible_ids],
                      dtype=float)
    weight_vector = np.array([weights[c] for c in criteria], dtype=float)
    directions = np.array([next(x["direction"] for x in D.CRITERIA
                                if x["id"] == c) for c in criteria], dtype=int)

    result = topsis(matrix, weight_vector, directions)

    # --- assemble ----------------------------------------------------------
    by_id = {a["id"]: a for a in D.ALTERNATIVES}
    ranking = []
    for row, alt in enumerate(eligible_ids):
        ranking.append({
            "alternative": by_id[alt]["name"],
            "id": alt,
            "sensor": by_id[alt]["sensor"],
            "closeness": round(float(result["closeness"][row]), 12),
            "rank": int(result["ranks"][row]),
            "d_plus": round(float(result["d_plus"][row]), 12),
            "d_minus": round(float(result["d_minus"][row]), 12),
        })
    ranking.sort(key=lambda r: (r["rank"], r["alternative"]))

    excluded = [{
        "alternative": by_id[a]["name"], "id": a, "sensor": by_id[a]["sensor"],
        "reason": eligibility[a]["reason"],
    } for a in D.ALTERNATIVE_IDS if not eligibility[a]["eligible"]]

    contested = [{
        "criterion": crit, "alternative": by_id[alt]["name"],
        "score": cell["score"], "basis": cell["basis"],
        "rationale": cell["rationale"],
        "weight": round(weights[crit], 6),
    } for (crit, alt), cell in sorted(D.EVIDENCE.items())
        if cell["contested"] and alt in eligible_ids]

    return {
        "method": {
            "name": "TOPSIS",
            "mode": mode,
            "mode_note": (
                "Indices scale criterion weights; the product scores are "
                "unchanged by the event."
                if mode == "WEIGHT" else
                "Indices multiply the matrix cells. Under vector "
                "normalisation a uniform column multiplier CANCELS, so this "
                "mode is insensitive to the terrain indices -- see the module "
                "docstring."),
            "wsm_implemented": False,
            "wsm_note": ("The specification also describes a weighted-sum "
                         "model as a robustness check and a Spearman "
                         "comparison of the two rankings. Neither is "
                         "implemented, so nothing here is method-agnostic."),
            "weighting": weighting,
            "weighting_rule": (WEIGHTING_RULE if weighting == "event_first" else
                               "Specification workbook v3: base weight x modulator for "
                               "every criterion, renormalised to sum to one."),
            "normalisation": "vector (Euclidean column norm)",
            "rank_rule": "competition ranking; ties share the better rank",
        },
        "indices": {
            "cloud_pct": cloud_pct, "wind_ms": wind_ms, "veg_pct": veg_pct,
            "steep_pct": steep_pct, "urban_pct": urban_pct,
        },
        "modulators": {c: round(mods[c], 12) for c in criteria},
        "parameters": params,
        "parameter_notes": D.PARAMETER_NOTES,
        "gate": {
            "cloud_gate_pct": gate,
            # At 100 the gate cannot fire: cloud cover has nowhere above to
            # go. Said explicitly, because "nothing was excluded" and "nothing
            # CAN be excluded" are different facts about a ranking.
            "can_fire": gate < 100.0,
            "change_note": D.CLOUD_GATE_CHANGE,
            "triggered": bool(excluded),
            "eligible": [by_id[a]["name"] for a in eligible_ids],
            "excluded": excluded,
        },
        # `modulated_by` is the index NAME and `modulator` its value. Both,
        # because they answer different questions: a modulator of 1.0 can mean
        # "this criterion is never modulated" or "its index is at maximum", and
        # over a 92%-cloud event W1 is emphatically the second.
        "criteria": [
            {**c, "base_weight": round(base[c["id"]], 12),
             "modulated_by": c["modulator"],
             "modulator": mods[c["id"]],
             "increase": (round(increase[c["id"]], 12) if c["id"] in increase else None),
             "effective_weight": round(effective[c["id"]], 12),
             "computed_weight": round(computed[c["id"]], 12),
             "weight": round(weights[c["id"]], 12)}
            for c in D.CRITERIA
        ],
        # Module 7: whose weights ranked the products (user decision 2026-11-19).
        "weights_source": (f"user-stated at {stated_at}" if changed
                           else "computed (as Module 6 showed)"),
        "weights_changed": changed,
        # The user's own scores (2026-11-22): every cell changed, with the
        # value it replaced. The evidence register keeps the documented one.
        "scores_source": (f"user-stated at {stated_at}" if scores_changed
                          else "evidence register"),
        "scores_changed": scores_changed,
        "user_choices": {"weights": dict(user_weights or {}),
                         "scores": {f"{c}|{a}": v for (c, a), v in score_changes.items()}},
        "alternatives": [
            {**by_id[a], "eligible": eligibility[a]["eligible"],
             "product_sources": D.PRODUCT_SOURCES[a],
             "p2_range_days": list(D.P2_RANGE[a])}
            for a in D.ALTERNATIVE_IDS
        ],
        "decision_matrix": {
            "values": {a: {c: applied[a][c] for c in criteria}
                       for a in D.ALTERNATIVE_IDS},
            "static_values": {a: dict(D.STATIC_MATRIX[a])
                              for a in D.ALTERNATIVE_IDS},
            "directions": {c: int(d) for c, d in zip(criteria, directions)},
            "evidence": {f"{crit}|{alt}": {**cell,
                                           "alternative": by_id[alt]["name"]}
                         for (crit, alt), cell in sorted(D.EVIDENCE.items())},
            "basis_meaning": D.BASIS_MEANING,
            "contested_cells": contested,
            "p2_pending": D.P2_PENDING,
        },
        "topsis": {
            "row_order": [by_id[a]["name"] for a in eligible_ids],
            "criterion_order": criteria,
            "normalised": result["normalised"].round(12).tolist(),
            "weighted": result["weighted"].round(12).tolist(),
            "ideal_best": {c: round(float(v), 12)
                           for c, v in zip(criteria, result["pis"])},
            "ideal_worst": {c: round(float(v), 12)
                            for c, v in zip(criteria, result["nis"])},
        },
        "ranking": ranking,
        **_closeness_figure(ranking, output_dir, event_id),
        "sources": D.SOURCES,
        "spec": {
            "workbook": "flood_dataset_MCDM_template_v3.xlsx",
            "built": "2026-09-22",
            "note": "Alternatives, criteria and evidence are ported from that "
                    "workbook; this module implements steps 1-7 of its Spec "
                    "sheet. Steps 8-9 (WSM and the Spearman comparison) are "
                    "not implemented.",
        },
    }


def _score_overrides(scores: dict[str, Any] | None) -> dict[tuple[str, str], float]:
    """The user's scores as {(criterion, alternative id): value}, checked.

    Accepts {"P2": {"gfm": 6}} or {"P2|gfm": 6}; a product by id or by name.
    A score outside its criterion's scale is refused, not clipped.
    """
    if not scores:
        return {}
    if not isinstance(scores, dict):
        raise ValueError('scores must be a mapping, e.g. {"P2": {"gfm": 6}}')
    by_name = {a["name"].lower(): a["id"] for a in D.ALTERNATIVES}
    by_name.update({a["id"].lower(): a["id"] for a in D.ALTERNATIVES})
    flat: list[tuple[str, str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, dict):
            flat += [(str(key), str(alt), v) for alt, v in value.items()]
        elif "|" in str(key):
            crit, alt = str(key).split("|", 1)
            flat.append((crit, alt, value))
        else:
            raise ValueError(f"score {key!r} names no product; use "
                             '{"P2": {"gfm": 6}} or {"P2|gfm": 6}')
    scale = {c["id"]: c["scale"] for c in D.CRITERIA}
    out: dict[tuple[str, str], float] = {}
    for crit, alt, value in flat:
        crit = crit.strip().upper()
        if crit not in scale:
            raise ValueError(f"scores names unknown criterion {crit!r}; known: "
                             f"{list(D.CRITERION_IDS)}")
        alt_id = by_name.get(alt.strip().lower())
        if alt_id is None:
            raise ValueError(f"scores names unknown product {alt!r}; known: "
                             f"{list(D.ALTERNATIVE_IDS)}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"score {crit}|{alt_id} must be a number, got {value!r}")
        value = float(value)
        if scale[crit].startswith("binary") and value not in (0.0, 1.0):
            raise ValueError(f"{crit} is scored 0 or 1, got {value:g}")
        if "0-2" in scale[crit] and not 0.0 <= value <= 2.0:
            raise ValueError(f"{crit} is scored 0 to 2, got {value:g}")
        if value < 0:
            raise ValueError(f"{crit} cannot be negative, got {value:g}")
        out[(crit, alt_id)] = value
    return out


def _module6_choices() -> dict[str, Any]:
    """The weights and scores the user set at Module 6, from the store this
    run writes to; {} when there is none or nothing was changed."""
    try:
        from .. import experiment

        store = experiment.current()
        record = store.cached_input("mcdm_decision_matrix") if store is not None else None
    except Exception:                                          # noqa: BLE001
        return {}
    payload = (record or {}).get("payload") or {}
    kept = payload.get("user_choices") or {}
    if not (kept.get("weights") or kept.get("scores")):
        return {}
    return {"weights": kept.get("weights") or None, "scores": kept.get("scores") or None}


def _closeness_figure(ranking: list[dict[str, Any]], output_dir: str,
                      event_id: str) -> dict[str, Any]:
    """Draw the closeness meter when a folder is given; never fails the ranking."""
    if not output_dir:
        return {}
    from pathlib import Path

    from .. import charts

    png = Path(output_dir) / (event_id or "mcdm") / "topsis_closeness.png"
    try:
        charts.closeness_meter(ranking, png)
    except Exception as exc:                                   # noqa: BLE001
        return {"figure_problem": f"{type(exc).__name__}: {exc}"}
    return {"figure": {
        "name": "topsis_closeness", "path": str(png),
        "caption": "TOPSIS closeness: each product's place between the anti-ideal "
                   "(0, worst on every criterion) and the ideal (1, best on every "
                   "criterion)."}}


@workflow(
    "mcdm_decision_matrix",
    "Module 6: the decision matrix for one event, BEFORE any ranking -- what each "
    "of the five NRT flood products scores on each of the twelve criteria, the "
    "evidence and source behind every score, and each criterion's weight: the "
    "equal base weight scaled by this event's cloud cover, wind speed, "
    "dense-vegetation, steep-terrain and built-up shares. Same five indices as "
    "rank_flood_products. Ranking is the next step (rank_flood_products, Module 7). "
    "The user may change any weight (`weights`, e.g. {\"L1\": 0.2}) or score "
    "(`scores`, e.g. {\"P2\": {\"gfm\": 6}}) here; the ranking then uses those "
    "changes. Pass `output_dir` and `event_id` to draw the matrix as one figure.",
    label="MCDM decision matrix",
    source="MCDM template v3",
    order=55,
    data_source_uncertainty=(
        "What: 30 evidence scores, five contested and some resting on author rules "
        "with no document behind them; the SAR revisit score (P2) is unresolved, "
        "and the five event values carry the uncertainty of weather, land cover and "
        "terrain. Affects: every score and weight shown. Reduce: settle the contested "
        "scores and P2 with a second coder; source the author rules."),
    method_uncertainty=(
        "What: the equal 1/12 base weights are a placeholder, and the event "
        "modulators (cloud curve, wind ramp, shares used directly) are author rules. "
        "Affects: how much each criterion counts in the ranking that follows. "
        "Reduce: decide AHP or entropy base weights; read the weights with the "
        "sensitivity analysis (Module 8)."),
)
def mcdm_decision_matrix(
    cloud_pct: float,
    wind_ms: float,
    veg_pct: float,
    steep_pct: float,
    urban_pct: float,
    mode: str = "WEIGHT",
    base_weights: dict[str, float] | list[float] | None = None,
    parameters: dict[str, float] | None = None,
    event_id: str = "",
    weights: dict[str, float] | None = None,
    scores: dict[str, Any] | None = None,
    output_dir: str = "",
    weighting: str = "event_first",
) -> dict[str, Any]:
    """Module 6 (user decision 2026-11-19): the scores and the weights as a step
    of their own, so the user sees them before anything is ranked.

    Since 2026-11-22 the user may change any weight or score HERE
    (`weights`, `scores`, the same shapes as rank_flood_products); the
    changes are kept in `user_choices`, and the ranking (Module 7) ranks
    with them unless told otherwise. A call without them shows the computed
    weights and the documented scores -- an earlier change is not carried.

    Built by the same code path as the ranking, so the matrix and weights here
    are exactly the ones Module 7 starts from; the ranking itself is left out
    of the result. With `output_dir`, the matrix is drawn as one figure
    (`<output_dir>/<event_id>/mcdm_decision_matrix.png`): benefit and cost
    criteria apart, each cell coloured by how it compares on its row, each
    weight beside the equal share it started from.
    """
    full = rank_flood_products(cloud_pct=cloud_pct, wind_ms=wind_ms, veg_pct=veg_pct,
                               steep_pct=steep_pct, urban_pct=urban_pct, mode=mode,
                               base_weights=base_weights, parameters=parameters,
                               weights=weights, scores=scores, use_module6=False,
                               weighting=weighting)
    out = {k: v for k, v in full.items()
           if k not in ("ranking", "topsis", "figure", "figure_problem")}
    for key, value in (("weights_source", full["weights_source"]),
                       ("scores_source", full["scores_source"])):
        out[key] = value.replace("Module 7", "Module 6")
    out["stage"] = ("Module 6: the decision matrix and the weights, before any ranking. "
                    "Any weight or score may be changed here or at Module 7; the ranking "
                    "uses the changes made here.")
    out["method"] = {**out["method"], "ranked": False}
    out["reading_guide"] = reading_guide(out)
    out.update(_matrix_figure(out, output_dir, event_id))
    return out


def reading_guide(payload: dict[str, Any]) -> list[str]:
    """Plain lines on how to read Module 6, from the payload's own values --
    what the answer opens with, so it never has to work them out."""
    crit = {c["id"]: c for c in payload.get("criteria") or []}
    benefit = [i for i, c in crit.items() if c.get("direction") == 1]
    cost = [i for i, c in crit.items() if c.get("direction") != 1]
    ind = payload.get("indices") or {}
    lines = [
        f"Benefit criteria ({', '.join(benefit)}): a higher score is better. "
        f"Cost criteria ({', '.join(cost)}): a lower score is better.",
    ]
    if (payload.get("method") or {}).get("weighting", "event_first") == "event_first":
        def up(cid: str) -> str:
            inc = float(crit[cid].get("increase") or 0.0)
            return f"+{inc * 100:.0f}%" if inc >= 0.005 else "+0%"
        lines += [
            "Event criteria are weighted first, each from 1/12 (0.083) raised by this "
            f"event: cloud {ind.get('cloud_pct', 0):.3g}% (mean at the optical passes) raises "
            f"W1 {up('W1')} "
            f"(to {float(crit['W1']['weight']):.3f}), dense vegetation "
            f"{ind.get('veg_pct', 0):.3g}% raises L1 {up('L1')} "
            f"(to {float(crit['L1']['weight']):.3f}), steep land {ind.get('steep_pct', 0):.3g}% "
            f"raises L2 {up('L2')}, built-up {ind.get('urban_pct', 0):.3g}% raises L3 "
            f"{up('L3')}, wind {ind.get('wind_ms', 0):.3g} m/s (mean at the radar passes) "
            f"raises W2 {up('W2')}; "
            "daylight (W3) stays at 1/12.",
            f"The weight left ({sum(float(crit[c]['weight']) for c in crit if c.startswith('P')):.3f})"
            f" is shared equally by the six product criteria P1-P6: "
            f"{float(crit['P1']['weight']):.3f} each.",
        ]
    else:
        lines.append("Weights: the specification workbook's rule -- base weight times the "
                     "event modulator, renormalised.")
    changed = [c["id"] for c in payload.get("weights_changed") or []
               if c.get("stated") is not None]
    if changed:
        lines.append(f"You set {', '.join(changed)} yourself; the others were rescaled to "
                     "keep the total at 1.")
    lines.append("Next, Module 7 (TOPSIS) ranks the products by how close each comes to an "
                 "imaginary product that is best on every row, counting each row by its weight.")
    return lines


def _matrix_figure(payload: dict[str, Any], output_dir: str, event_id: str) -> dict[str, Any]:
    """Draw Module 6 when a folder is given; never fails the matrix."""
    if not output_dir:
        return {}
    from pathlib import Path

    from .. import charts

    png = Path(output_dir) / (event_id or "mcdm") / "mcdm_decision_matrix.png"
    try:
        charts.decision_matrix_figure(payload, png)
    except Exception as exc:                                   # noqa: BLE001
        return {"figure_problem": f"{type(exc).__name__}: {exc}"}
    return {"figure": {
        "name": "mcdm_decision_matrix", "path": str(png),
        "caption": "Module 6: each product's scores, benefit criteria (higher is better) "
                   "apart from cost criteria (lower is better), coloured by how each "
                   "compares on its row; each weight beside the equal share it started "
                   "from, and what this event did to it."}}
