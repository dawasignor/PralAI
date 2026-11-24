"""The static half of the NRT flood-product decision: alternatives, criteria, evidence.

Ported from `flood_dataset_MCDM_template_v3.xlsx` (built 2026-09-22), which is
the specification. **Every score here carries its basis, its rationale and its
source key**, because a decision matrix without those is a table of numbers
somebody has to take on trust, and the thesis is about not doing that.

Three things are static and live here; everything that changes with the event
lives in `mcdm.py`:

* **Product criteria P1-P6** -- spatial resolution, revisit, flood separation, uncertainty
  layer, archive length, access maturity (renamed 2026-11-19 from
  posting, uncertainty layer richness and archive depth). Properties of the product, not of
  the flood.
* **Susceptibilities W1-W3 and L1-L3** (land characteristics; T1-T3 before 2026-11-19) -- how badly each product is hurt by
  cloud, wind, darkness, canopy, terrain and buildings. Also properties of the
  product; the EVENT decides how much each one matters.
* **The evidence register** -- 30 cells, one per (criterion, alternative), each
  with `basis`, `contested`, `rationale` and `sources`.

`basis` is the honest part of the register and is not decoration:

    D  documentation   the product's own spec or user guide says so
    S  sensor physics  stated for one product, applied by sensor type
    R  author rule     a modelling choice, with no document behind it

**Five cells are marked `contested`.** (Every count in this docstring is
asserted against the register at import -- see `PUBLISHED_COUNTS`. If one is
wrong the module refuses to load.) They are the ones a second coder could
reasonably score differently, and they are carried into the output rather than
smoothed over. A sensitivity analysis that does not know which cells are soft
is testing the wrong thing.

**P2 is unresolved.** The author's instruction on SAR revisit was cut off, so
GFM and DSWx-S1 keep the documented 6-12 day range and the worst case (12) is
used. That is recorded as `pending` and travels into the output.
"""

from __future__ import annotations

# --- alternatives ----------------------------------------------------------
#
# Five, and the set is closed at v3. A candidate was withdrawn when the set was
# settled, and the licence-openness criterion went with it -- that criterion had
# zero variance across the survivors, so it could not discriminate and was
# dropped rather than carried as a column of identical scores. Why the set is
# the set it is belongs in the record, not here: see the
# vault's "Excluded candidates" section in [[NRT products]]. Do not re-add the
# criterion without re-reading that.
ALTERNATIVES: tuple[dict[str, str], ...] = (
    {"id": "gfm", "name": "GFM (Sentinel-1)", "sensor": "SAR",
     "note": "Copernicus Global Flood Monitoring, Sentinel-1 C-band"},
    {"id": "dswx_hls", "name": "OPERA DSWx-HLS", "sensor": "Optical",
     "note": "OPERA Dynamic Surface Water Extent from Harmonised Landsat-Sentinel"},
    {"id": "dswx_s1", "name": "OPERA DSWx-S1", "sensor": "SAR",
     "note": "OPERA Dynamic Surface Water Extent from Sentinel-1"},
    {"id": "mcdwd", "name": "MODIS MCDWD", "sensor": "Optical",
     "note": "LANCE MODIS NRT Global Flood Product"},
    {"id": "vcdwd", "name": "VIIRS VCDWD", "sensor": "Optical",
     "note": "LANCE VIIRS NRT Global Flood Product"},
)

# --- criteria --------------------------------------------------------------
#
# `direction` is +1 for a benefit (more is better) and -1 for a cost. Every
# weather and terrain criterion is a cost: they measure susceptibility, and
# being more susceptible is never better.
#
# `modulator` names which event index scales this criterion. None means the
# criterion is not modulated -- the product criteria and W3, which do not
# depend on the weather.
CRITERIA: tuple[dict, ...] = (
    {"id": "P1", "name": "Spatial resolution", "group": "Product",
     "direction": -1, "scale": "ratio (m)", "modulator": None,
     "rule": "Grid spacing of the delivered product, from the product "
             "specification."},
    {"id": "P2", "name": "Revisit interval (worst case)", "group": "Product",
     "direction": -1, "scale": "ratio (days)", "modulator": None,
     "rule": "Documented revisit range at the case-study latitude; the value "
             "used is the maximum, i.e. the worst case."},
    {"id": "P3", "name": "Separates flood from permanent water",
     "group": "Product", "direction": 1, "scale": "binary 0/1",
     "modulator": None,
     "rule": "1 = the product removes or flags permanent and seasonal water; "
             "0 = it maps total surface water."},
    {"id": "P4", "name": "Uncertainty layer", "group": "Product",
     "direction": 1, "scale": "ordinal 0-2", "modulator": None,
     "rule": "2 = continuous per-pixel likelihood or diagnostic; "
             "1 = categorical confidence only; 0 = none."},
    {"id": "P5", "name": "Archive length", "group": "Product",
     "direction": 1, "scale": "ratio (years)", "modulator": None,
     "rule": "Years from the start of the continuous record to 2026."},
    {"id": "P6", "name": "Programmatic access maturity", "group": "Product",
     "direction": 1, "scale": "ordinal 0-2", "modulator": None,
     "rule": "2 = STAC/CMR plus a cloud-optimised format plus API or object "
             "storage; 1 = authenticated bulk download, non-COG; "
             "0 = manual or order-only."},
    {"id": "W1", "name": "Cloud cover", "group": "Weather",
     "direction": -1, "scale": "binary susceptibility x index",
     "modulator": "cloud",
     "rule": "Susceptibility: 1 = optical (cloud-blocked), 0 = SAR. The index "
             "g(c) is 0 up to 20% cloud and rises convexly to 1 at 100%. "
             "Since 2026-09-22 there is no exclusion: cloud penalises an "
             "optical product's weight, it does not remove the product."},
    {"id": "W2", "name": "Wind speed", "group": "Weather",
     "direction": -1, "scale": "binary susceptibility x index",
     "modulator": "wind",
     "rule": "Susceptibility: 1 = SAR, 0 = optical. Author rule: wind mainly "
             "affects SAR, by roughening the water surface. The index f(w) is "
             "0 up to 5 m/s and rises linearly to 1 at 15 m/s."},
    {"id": "W3", "name": "Solar illumination dependence", "group": "Weather",
     "direction": -1, "scale": "binary 0/1", "modulator": None,
     "rule": "1 = requires daylight; 0 = observes day and night. Not "
             "modulated: darkness happens every night regardless of the "
             "event."},
    {"id": "L1", "name": "Forest and vegetation", "group": "Land characteristics",
     "direction": -1, "scale": "ordinal 0-2 x index", "modulator": "veg",
     "rule": "Ordinal susceptibility times the share of the AOI with NDVI >= 0.60 "
             "(the very-high-greenness class; breaks after Aryal et al. 2022)."},
    {"id": "L2", "name": "Mountain / steep terrain", "group": "Land characteristics",
     "direction": -1, "scale": "ordinal 0-2 x index", "modulator": "steep",
     "rule": "Ordinal susceptibility times the share of the AOI classified "
             "steep or very steep (slope >= 15 degrees, Horn's method)."},
    {"id": "L3", "name": "Urban area", "group": "Land characteristics",
     "direction": -1, "scale": "ordinal 0-2 x index", "modulator": "urban",
     "rule": "Ordinal susceptibility times the share of the AOI with "
             "BUI > 0.2 -- built-up, roads, asphalt."},
)

# --- the static matrix -----------------------------------------------------
#
# Rows follow ALTERNATIVES, columns follow CRITERIA. P2 holds the worst case
# (the maximum of the documented range); the range itself is in P2_RANGE so
# the output can show what was collapsed.
STATIC_MATRIX: dict[str, dict[str, float]] = {
    "gfm":      {"P1": 20,  "P2": 12, "P3": 1, "P4": 2, "P5": 11, "P6": 2,
                 "W1": 0, "W2": 1, "W3": 0, "L1": 2, "L2": 2, "L3": 2},
    "dswx_hls": {"P1": 30,  "P2": 7,  "P3": 0, "P4": 1, "P5": 3,  "P6": 2,
                 "W1": 1, "W2": 0, "W3": 1, "L1": 2, "L2": 1, "L3": 1},
    "dswx_s1":  {"P1": 30,  "P2": 12, "P3": 0, "P4": 2, "P5": 2,  "P6": 2,
                 "W1": 0, "W2": 1, "W3": 0, "L1": 1, "L2": 2, "L3": 2},
    "mcdwd":    {"P1": 250, "P2": 1,  "P3": 1, "P4": 0, "P5": 23, "P6": 1,
                 "W1": 1, "W2": 0, "W3": 1, "L1": 2, "L2": 1, "L3": 1},
    "vcdwd":    {"P1": 250, "P2": 1,  "P3": 1, "P4": 0, "P5": 1,  "P6": 1,
                 "W1": 1, "W2": 0, "W3": 1, "L1": 2, "L2": 1, "L3": 1},
}

# The documented revisit range, before the worst case was taken. Reported so a
# reader can see that GFM's 12 is the bad end of 6-12, not a measurement.
P2_RANGE: dict[str, tuple[float, float]] = {
    "gfm": (6, 12), "dswx_hls": (1, 7), "dswx_s1": (6, 12),
    "mcdwd": (1, 1), "vcdwd": (1, 1),
}

P2_PENDING = ("The author's instruction on SAR revisit was cut off when the "
              "specification was written. GFM and OPERA DSWx-S1 keep the "
              "documented 6-12 day range with the worst case (12 days) used. "
              "This is unresolved, not settled -- it favours the optical "
              "products on P2 and should be revisited before the ranking is "
              "cited.")

# Where each row's P1-P6 values come from. The W/T cells are sourced
# individually in EVIDENCE below.
PRODUCT_SOURCES: dict[str, str] = {
    "gfm": "GFM Product Definition Document / EODC wiki; Wagner et al. 2026 (S1)",
    "dswx_hls": "PO.DAAC DSWx-HLS collection; DSWx-HLS Product Specification",
    "dswx_s1": "DSWx-S1 Product Specification v1.0.0 (S6); PO.DAAC DSWx-S1 collection",
    "mcdwd": "LANCE MCDWD/VCDWD User Guide (S3); LAADS product description",
    "vcdwd": "LANCE MCDWD/VCDWD User Guide (S3); LANCE NRT Global Flood Products page",
}

# --- the evidence register -------------------------------------------------
#
# 30 cells: the six susceptibility criteria times the five alternatives. Keyed
# (criterion, alternative). `score` here must equal STATIC_MATRIX -- checked at
# import, because two copies of a number are one copy and one bug waiting.
EVIDENCE: dict[tuple[str, str], dict] = {
    ("W1", "gfm"): dict(score=0, basis="D", contested=False, sources=["S1"],
        rationale="SAR; maps floods under cloud cover."),
    ("W1", "dswx_hls"): dict(score=1, basis="D", contested=False, sources=["S4"],
        rationale="Optical; the water layer inherits HLS Fmask cloud and "
                  "cloud-shadow labels, dilated."),
    ("W1", "dswx_s1"): dict(score=0, basis="D", contested=False, sources=["S5"],
        rationale="SAR; maps water irrespective of cloud."),
    ("W1", "mcdwd"): dict(score=1, basis="D", contested=False, sources=["S2", "S11"],
        rationale="Optical; cloud obscures flood detection."),
    ("W1", "vcdwd"): dict(score=1, basis="D", contested=False, sources=["S2"],
        rationale="Optical; same algorithm as MCDWD."),

    ("W2", "gfm"): dict(score=1, basis="D", contested=False, sources=["S1"],
        rationale="Wind-roughened water raises backscatter and can remove the "
                  "water/land contrast; GFM flags it but keeps the pixels."),
    ("W2", "dswx_hls"): dict(score=0, basis="R", contested=False, sources=[],
        rationale="Author rule: the wind penalty applies to SAR only."),
    ("W2", "dswx_s1"): dict(score=1, basis="S", contested=False, sources=["S1"],
        rationale="Same C-band wind-roughening physics as GFM."),
    ("W2", "mcdwd"): dict(score=0, basis="R", contested=False, sources=[],
        rationale="Author rule: the wind penalty applies to SAR only."),
    ("W2", "vcdwd"): dict(score=0, basis="R", contested=False, sources=[],
        rationale="Author rule: the wind penalty applies to SAR only."),

    ("W3", "gfm"): dict(score=0, basis="D", contested=False, sources=["S1"],
        rationale="Radar; operates in poor light."),
    ("W3", "dswx_hls"): dict(score=1, basis="D", contested=False, sources=["S10"],
        rationale="Optical surface reflectance; daytime only."),
    ("W3", "dswx_s1"): dict(score=0, basis="D", contested=False, sources=["S5"],
        rationale="Maps water irrespective of daylight."),
    ("W3", "mcdwd"): dict(score=1, basis="D", contested=False, sources=["S11"],
        rationale="Observations at about 10:30 and 13:30 local time."),
    ("W3", "vcdwd"): dict(score=1, basis="D", contested=False, sources=["S2"],
        rationale="Daytime optical; same algorithm as MCDWD."),

    ("L1", "gfm"): dict(score=2, basis="D", contested=False, sources=["S1"],
        rationale="Dense vegetation sits in the exclusion mask (the "
                  "no-sensitivity sub-mask); C-band is insensitive beneath "
                  "dense canopy."),
    ("L1", "dswx_hls"): dict(score=2, basis="S", contested=False, sources=["S2", "S10"],
        rationale="Optical cannot observe water beneath canopy; OPERA "
                  "positions SAR for mapping beneath vegetation."),
    ("L1", "dswx_s1"): dict(score=1, basis="D", contested=False, sources=["S5", "S10"],
        rationale="Explicit inundated-vegetation class; SAR maps beneath some "
                  "vegetation."),
    ("L1", "mcdwd"): dict(score=2, basis="D", contested=False, sources=["S2", "S11"],
        rationale="Canopy cover obscures the underlying water."),
    ("L1", "vcdwd"): dict(score=2, basis="D", contested=False, sources=["S2", "S11"],
        rationale="Canopy cover obscures the underlying water."),

    ("L2", "gfm"): dict(score=2, basis="D", contested=False, sources=["S1"],
        rationale="Radar-shadow and topographic-distortion areas are masked "
                  "out."),
    ("L2", "dswx_hls"): dict(score=1, basis="D", contested=True, sources=["S4", "S3"],
        rationale="Ships a terrain-shadow layer; terrain shadow is spectrally "
                  "similar to water. Whether it is masked or merely flagged is "
                  "not confirmed."),
    ("L2", "dswx_s1"): dict(score=2, basis="D", contested=False, sources=["S6"],
        rationale="The layover/shadow mask removes affected areas."),
    ("L2", "mcdwd"): dict(score=1, basis="D", contested=False, sources=["S3", "S11"],
        rationale="Terrain-shadow false positives; compositing is much less "
                  "effective on them, and the HAND mask reduces but does not "
                  "remove them."),
    ("L2", "vcdwd"): dict(score=1, basis="D", contested=False, sources=["S2", "S3"],
        rationale="Same algorithm as MCDWD."),

    ("L3", "gfm"): dict(score=2, basis="D", contested=False, sources=["S1"],
        rationale="Urban areas sit in the no-sensitivity exclusion sub-mask; "
                  "dense urban flooding is not mappable."),
    ("L3", "dswx_hls"): dict(score=1, basis="S", contested=True, sources=["S2"],
        rationale="Buildings obscure optical flood detection; stated for "
                  "MODIS/VIIRS and applied here by sensor type."),
    ("L3", "dswx_s1"): dict(score=2, basis="S", contested=True, sources=["S1"],
        rationale="C-band is insensitive to inundation in dense urban areas; "
                  "explicit urban masking in DSWx-S1 is not confirmed."),
    ("L3", "mcdwd"): dict(score=1, basis="D", contested=True, sources=["S2", "S11"],
        rationale="Buildings obscure flood detection; a second coder could "
                  "argue 2."),
    ("L3", "vcdwd"): dict(score=1, basis="D", contested=True, sources=["S2"],
        rationale="Same as MCDWD."),
}

BASIS_MEANING: dict[str, str] = {
    "D": "product documentation -- the product's own spec or user guide",
    "S": "sensor physics -- stated for one product, applied by sensor type",
    "R": "author rule -- a modelling choice, with no document behind it",
}

SOURCES: dict[str, dict[str, str]] = {
    "S1": {"ref": "Wagner et al. (2026). The fully-automatic Sentinel-1 Global "
                  "Flood Monitoring service: scientific challenges and future "
                  "directions. Remote Sensing of Environment 333, 115108.",
           "url": "https://www.sciencedirect.com/science/article/pii/S0034425725005127"},
    "S2": {"ref": "NASA Earthdata Forum: Flood Product FAQs (MODIS and VIIRS "
                  "usage notes).",
           "url": "https://forum.earthdata.nasa.gov/viewtopic.php?t=7707"},
    "S3": {"ref": "MODIS/VIIRS NRT Global Flood Products User Guide, Rev E.",
           "url": "https://www.earthdata.nasa.gov/s3fs-public/2025-04/"
                  "MCDWD_VCDWD_UserGuide_RevE_04.22.25.pdf"},
    "S4": {"ref": "Earthdata / PO.DAAC: OPERA DSWx-HLS V1 collection page, "
                  "known issues.",
           "url": "https://www.earthdata.nasa.gov/data/catalog/"
                  "pocloud-opera-l3-dswx-hls-v1-1.0"},
    "S5": {"ref": "OPERA DSWx-S1 V1 collection description (Earth Engine "
                  "catalogue).",
           "url": "https://developers.google.com/earth-engine/datasets/"
                  "catalog/OPERA_DSWX_L3_V1_S1"},
    "S6": {"ref": "OPERA DSWx-S1 Product Specification Document v1.0.0, "
                  "D-108761 Rev A.",
           "url": "https://d2pn8kiwq2w21t.cloudfront.net/documents/"
                  "OPERA_DSWx-S1_Product_Spec_v1.0.0_D-108761_RevA_2024-08-16_.pdf"},
    "S10": {"ref": "NASA Earthdata: OPERA Near-Global DSWx Product Suite.",
            "url": "https://www.earthdata.nasa.gov/data/projects/nsite/"
                   "solutions/opera-near-global-dswx-product-suite"},
    "S11": {"ref": "LANCE MODIS NRT Global Flood Product description page.",
            "url": "https://floodmap.modaps.eosdis.nasa.gov/proddesc.php"},
}

# --- parameters ------------------------------------------------------------
#
# Author rules, every one of them. None is a measurement, and the output says
# so, because a reader who assumes the 80% gate is empirical will over-trust
# the exclusions it produces.
PARAMETERS: dict[str, float] = {
    "cloud_lower": 20.0,
    "cloud_upper": 100.0,
    "cloud_gate": 100.0,
    "cloud_k": 3.0,
    "wind_lower": 5.0,
    "wind_upper": 15.0,
    "bui_threshold": 0.2,
}

PARAMETER_NOTES: dict[str, str] = {
    "cloud_lower": "Author rule: the optical penalty is zero up to 20% cloud.",
    "cloud_upper": "Author rule, CHANGED 2026-09-22 from 80 to 100: the "
                   "penalty now reaches 1 only at total cloud. It is a "
                   "continuous penalty across the whole range rather than one "
                   "that saturates at 80%.",
    "cloud_gate": "Author rule, CHANGED 2026-09-22 from 80 to 100: at 100 the "
                  "gate can no longer fire, because cloud cover cannot exceed "
                  "100%. Optical products are now always carried into the "
                  "ranking and penalised, never removed. See "
                  "CLOUD_GATE_CHANGE.",
    "cloud_k": "Author choice: k = 3. With the upper bound at 100 the penalty "
               "is about 0.109 at 50% cloud, 0.289 at 70% and 0.445 at 80% -- "
               "the whole curve is gentler than it was against an 80% bound, "
               "where those points were 0.182, 0.586 and 1.0. Still convex: "
               "mild at moderate cloud, rising toward total cloud.",
    "wind_lower": "Author rule: the SAR penalty starts above 5 m/s.",
    "wind_upper": "Author rule: the SAR penalty reaches 1 at 15 m/s.",
    "bui_threshold": "Author rule, applied when the built-up share is derived "
                     "from the BUI histogram.",
}

# The change of 2026-09-22, recorded where the numbers are rather than in a
# commit message nobody reads beside the output.
CLOUD_GATE_CHANGE = (
    "Set by the user on 2026-09-22: penalise cloud up to 100% rather than "
    "rejecting above 80%. Both `cloud_upper` and `cloud_gate` moved from 80 "
    "to 100, and they do different things -- the first is where the penalty "
    "curve saturates, the second is where an optical product stopped being a "
    "candidate at all.\n\n"
    "**This is a change of method, not of tuning.** The specification treated "
    "cloud as a FEASIBILITY GATE: above 80% an optical product was removed, "
    "on the reasoning that a sensor which cannot see the ground is not a "
    "worse option but not an option. It is now a penalty only, so a product "
    "that would return almost nothing over a heavily clouded event (Kelani "
    "2025, the event this was decided on, averaged 92% cloud) is ranked "
    "rather than excluded, and it can still place well if its other "
    "criteria are strong.\n\n"
    "The gain is that the ranking is continuous in cloud and every candidate "
    "keeps a comparable score, which is what the thesis wanted. The cost is "
    "that eligibility no longer carries any information: a reader must check "
    "`W1` and the cloud index to know whether a highly-ranked optical product "
    "could actually have observed the event.")

CRITERION_IDS: tuple[str, ...] = tuple(c["id"] for c in CRITERIA)
ALTERNATIVE_IDS: tuple[str, ...] = tuple(a["id"] for a in ALTERNATIVES)


# --- the counts the prose quotes -------------------------------------------
#
# These numbers are written out in words in four documents, and prose cannot
# check itself. When a candidate was withdrawn at v3 the register lost a
# contested cell and two author-rule cells, and every document went on saying
# six and five for months -- the numbers were only caught by counting them by
# hand while writing output 5. A reader has no way to tell a stale count from
# a real one.
#
# So the counts are declared here and asserted against the register at import.
# Adding an evidence cell, flipping `contested`, or removing an alternative now
# fails the next run with a message naming the documents to update. Declared
# rather than derived on purpose: a derived count would silently follow the
# data and the prose would drift again, which is the failure being prevented.
PUBLISHED_COUNTS: dict[str, int] = {
    "alternatives": 5,
    "criteria": 12,
    "evidence_cells": 30,
    "contested": 5,
    "basis_D": 23,
    "basis_R": 3,
    "basis_S": 4,
}

# Where those numbers appear in words. Named so the error message can tell the
# author what to go and edit, rather than leaving them to grep for a digit.
PROSE_CITING_COUNTS: tuple[str, ...] = (
    "core/workflows/_mcdm_data.py -- this module's docstring",
    "docs/rank_flood_products.md -- the evidence register section",
    "docs/WORKFLOWS.md -- section 7, rank_flood_products",
    "docs/SOURCES.md -- section 8b, 'What is NOT sourced here'",
)


def _check_consistency() -> None:
    """The evidence register and the matrix must agree, cell for cell.

    They hold the same thirty numbers, and two copies of a number is one copy
    and one bug waiting. Checked at import so a typo in either is a crash on
    the next run rather than a silently different ranking.
    """
    for (crit, alt), cell in EVIDENCE.items():
        if crit not in CRITERION_IDS:
            raise ValueError(f"evidence names unknown criterion {crit!r}")
        if alt not in ALTERNATIVE_IDS:
            raise ValueError(f"evidence names unknown alternative {alt!r}")
        stated = STATIC_MATRIX[alt][crit]
        if float(cell["score"]) != float(stated):
            raise ValueError(
                f"evidence and matrix disagree at ({crit}, {alt}): "
                f"{cell['score']} vs {stated}")
        for key in cell["sources"]:
            if key not in SOURCES:
                raise ValueError(f"({crit}, {alt}) cites unknown source {key!r}")
        if cell["basis"] not in BASIS_MEANING:
            raise ValueError(f"({crit}, {alt}) has unknown basis {cell['basis']!r}")

    # Every susceptibility criterion must be fully coded, or a missing cell
    # becomes a silent zero.
    for crit in ("W1", "W2", "W3", "L1", "L2", "L3"):
        missing = [a for a in ALTERNATIVE_IDS if (crit, a) not in EVIDENCE]
        if missing:
            raise ValueError(f"criterion {crit} has no evidence for {missing}")

    for alt in ALTERNATIVE_IDS:
        absent = [c for c in CRITERION_IDS if c not in STATIC_MATRIX[alt]]
        if absent:
            raise ValueError(f"alternative {alt} has no value for {absent}")


def _check_published_counts() -> None:
    """The register must hold the numbers the documents claim it holds.

    Same argument as the evidence/matrix check above, one level out: a number
    stated in two places is one number and one bug waiting, and here the second
    place is prose that nothing executes. A count that has gone stale reads
    exactly like a correct one.
    """
    actual = {
        "alternatives": len(ALTERNATIVE_IDS),
        "criteria": len(CRITERION_IDS),
        "evidence_cells": len(EVIDENCE),
        "contested": sum(1 for cell in EVIDENCE.values() if cell["contested"]),
        **{f"basis_{code}": sum(1 for cell in EVIDENCE.values()
                                if cell["basis"] == code)
           for code in BASIS_MEANING},
    }
    drifted = {key: (stated, actual.get(key))
               for key, stated in PUBLISHED_COUNTS.items()
               if actual.get(key) != stated}
    if drifted:
        detail = "; ".join(f"{key}: documents say {stated}, register holds "
                           f"{found}" for key, (stated, found) in
                           sorted(drifted.items()))
        where = "\n  - ".join(PROSE_CITING_COUNTS)
        raise ValueError(
            f"the evidence register no longer matches the published counts "
            f"({detail}). This is not a data error -- the register is the "
            f"truth -- it is a signal that the prose quoting these numbers is "
            f"now wrong. Update PUBLISHED_COUNTS and every document below, "
            f"together:\n  - {where}")


_check_consistency()
_check_published_counts()
