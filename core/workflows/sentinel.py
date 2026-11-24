"""Sentinel-2 land-cover indices from the Earth Search STAC catalogue.

https://earth-search.aws.element84.com/v1  --  collection `sentinel-2-c1-l2a`

No authentication, no account, no asset signing: Element 84's catalogue points
straight at public COGs on S3, and rasterio reads a window out of them over
HTTPS. Scientific references for every index are in docs/SOURCES.md.

Scene selection is the part that matters. A flood is a cloudy-season event, so
the pre-event window is the worst window Sentinel-2 has -- over Shillong in
monsoon, every scene in a 15-day lookback was 71-100% cloud. So the search
walks backwards up to `lookback_days` and takes the MOST RECENT scene that is
actually usable over the AOI, rather than compositing or taking the clearest.
Recency wins because land cover is the pre-event baseline: a scene from three
weeks ago describes the catchment better than a clear one from three months
ago.

"Usable over the AOI" is not the same as the catalogue's `eo:cloud_cover`,
which is computed over the whole 110 km tile. A 99%-cloudy tile can have a
clear AOI and a 40%-cloudy tile can be solid cloud exactly where you are
looking. So cloud cover is only a cheap prefilter; the decision is made by
reading the scene's SCL band over the AOI.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import numpy as np

from . import workflow
from . import _raster as R
from . import landmask

CATALOGUE_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-c1-l2a"

# Scene Classification Layer, Sentinel-2 L2A Algorithm Theoretical Basis Doc.
# Valid = a real surface observation. Everything else is cloud, shadow,
# saturation or fill, and must not reach an index.
SCL_VALID = {4: "vegetation", 5: "not_vegetated", 6: "water",
             7: "unclassified", 11: "snow_ice"}
SCL_LABELS = {
    0: "no_data", 1: "saturated_defective", 2: "dark_area", 3: "cloud_shadow",
    4: "vegetation", 5: "not_vegetated", 6: "water", 7: "unclassified",
    8: "cloud_medium_prob", 9: "cloud_high_prob", 10: "thin_cirrus",
    11: "snow_ice",
}

# Which STAC asset each index needs. `nir` is B08 (10 m), `nir08` is B8A
# (20 m). Both are "NIR", and which one an index uses is not arbitrary --
# see the band-pairing note in docs/fetch_lulc_data.md.
_INDEX_BANDS = {
    "ndvi":  ("nir", "red"),
    "ndbi":  ("swir16", "nir08"),
    "ndmi":  ("nir", "swir16"),
    "mndwi": ("green", "swir16"),
    "nmdi":  ("nir08", "swir16", "swir22"),
    # TODO(arid-bui): see docs/arid_region.md -- bui is unvalidated over bare
    # ground and its direction is unsettled.
    "bui":   (),          # derived from ndbi and ndvi, reads no band itself
}
DEFAULT_INDICES = ["ndvi", "ndbi", "bui", "ndmi", "nmdi", "mndwi"]

# Share of the AOI that must be a real surface observation for a scene to be
# accepted.
#
# 0.7, set by the user on 2026-09-22. The history matters, because two of the
# three earlier settings were decided on a number that turned out to be false:
#
#   0.7 -> 0.9  raised on the grounds that this output feeds an MCDM score
#               rather than a human reader.
#   0.9 -> 0.8  lowered because "the best Kelani scene was 0.8515". It was not.
#               That figure came from the pre-fix `plan_grid` clamp, measured on
#               a window translated 1,258 px (25 km) east of the AOI -- drier
#               ground nobody had asked about. The same scene, measured on the
#               correct window, is 0.7287 over its own 0.904 of the AOI and
#               0.7357 over the whole AOI as a two-tile mosaic.
#   0.8 -> 0.7  set with the true figures in hand. Nothing in a 90-day lookback
#               over the Kelani exceeds 0.7357; the next best date is 0.3926.
#               So this is not a threshold chosen to admit a marginal scene --
#               it is the only band in which this catchment has a pre-event
#               observation at all, and the alternative is no baseline.
#
# The cost is real and is NOT hidden: every statistic is a mean over the
# surviving pixels, `aoi_valid_fraction` travels with it, and at this threshold
# nearly a third of an AOI may be absent from a figure that still reads like a
# whole-AOI mean. That is why the brief carries an uncertainty section rather
# than a footnote. Do not lower it further without the same conversation.
MIN_VALID_FRACTION = 0.7
# The step-down is off: 0.7 is the one target, everywhere (user decision
# 2026-11-01, replacing the 0.7 -> 0.6 -> 0.5 step-down of 2026-10-27).
# **Since 2026-11-11 (user decision) 0.7 is a TARGET, not a gate**: when no
# scene reaches it, fetch_lulc_data uses the CLEAREST scene in the window,
# measured at full resolution, and reports `selection.best_available`
# (target, measured fraction, unobserved share) as uncertainty. The WorldCover
# fallback (D-8) is now reached only when no scene has any clear land.
STEP_DOWN_FRACTIONS: tuple[float, ...] = ()
# The scene search walks back this many days from the event start, FIXED
# (user decision 2026-11-01). Monty had sent 30 for Bangkok, found nothing
# above 0.7 (best 0.3009) and fell back to WorldCover, while at 90 days the
# 18 Aug 2026 scene passes at 0.733. A window a model shortens is not a
# finding about the imagery.
LOOKBACK_DAYS = 90

# Every index is a normalised difference bounded to [-1, 1], except BUI (a
# difference of two of them) and NMDI (see compute_indices). Values outside
# these bounds are a bug, and the histogram range depends on them.
INDEX_RANGES = {
    "ndvi": (-1.0, 1.0), "ndbi": (-1.0, 1.0), "ndmi": (-1.0, 1.0),
    "mndwi": (-1.0, 1.0), "nmdi": (-1.0, 1.0), "bui": (-2.0, 2.0),
}

# NMDI is defined only where SWIR16 exceeds SWIR22. Below this gap, in
# reflectance units, the pixel is water, shadow or noise. See compute_indices.
NMDI_MIN_SWIR_GAP = 0.01

# NDVI bins, named by what NDVI measures -- greenness -- NOT by land-cover
# type. A single date cannot separate forest from shrub from cropland: in the
# growing season a closed canopy and a dense crop both sit near 0.8.
#
# The split that can separate them is phenological (seasonal NDVI amplitude
# across a year), specified in docs/ndvi_greenness_bins.md and not implemented:
# it needs a scene stack, not the single scene this function selects. Do not
# relabel these bins until that lands.
#
# **The breaks are Aryal et al. (2022)** (user decision 2026-11-19): their
# Level-3 NDVI thresholds for Sentinel-2 -- 0.19 non-vegetation / vegetation
# (Table 2), 0.50 shrub / trees (Table 3), 0.40 and 0.60 stressed / healthy
# (Tables 4 and 5). The classes keep GREENNESS names rather than the paper's
# shrub / trees, for the reason above: one date cannot tell them apart. Until
# 2026-11-19 the breaks were 0 / 0.15 / 0.35 / 0.60 with no paper behind them.
# The top break, 0.60, is unchanged, so the dense-vegetation share the
# ranking's L1 criterion reads is the same quantity as before.
NDVI_BINS = [
    (-1.01, 0.19, 1, "non_vegetation"),
    (0.19, 0.40, 2, "low_greenness"),
    (0.40, 0.50, 3, "moderate_greenness"),
    (0.50, 0.60, 4, "high_greenness"),
    (0.60, 1.01, 5, "very_high_greenness"),
]
# The class the ranking's L1 criterion counts as dense vegetation, and the
# name results stored before 2026-11-19 used for the same >= 0.60 class.
DENSE_CLASS, DENSE_CLASS_BEFORE = "very_high_greenness", "dense_vegetation"


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def _client():
    """STAC client, imported lazily so the repo runs without the EO stack."""
    import warnings

    try:
        from pystac_client import Client
    except ImportError as exc:                                # pragma: no cover
        raise RuntimeError(
            "sentinel workflows need: pip install pystac-client rasterio numpy"
        ) from exc

    # Earth Search advertises plain HTTPS hrefs rather than s3://, so pystac's
    # storage extension cannot parse a bucket out of them and warns once per
    # asset. Harmless, and it would otherwise bury Monty's logbook output.
    warnings.filterwarnings(
        "ignore", message="Could not parse bucket/account from href",
        module="pystac.extensions.storage")
    return Client.open(CATALOGUE_URL)


def _asset_scaling(asset, name: str = "?") -> tuple[float, float, float | None]:
    """Scale, offset and nodata from a STAC asset's `raster:bands`.

    Read, never hardcoded. Processing baseline 05.00+ carries a BOA offset of
    -0.1, and that offset does not cancel out of a normalised difference -- the
    numerator cancels but the denominator does not. Getting it from the item
    means a future baseline change corrects itself.

    Missing metadata raises rather than defaulting to scale 1 / offset 0, and
    that is deliberate. Indices computed on raw uint16 DN still land inside
    [-1, 1], so a silent default would sail past every range check in the test
    suite while every value was wrong. There is no assertion that catches this
    downstream -- it has to fail here.
    """
    extra = getattr(asset, "extra_fields", {}) or {}
    bands = extra.get("raster:bands")
    if not bands or "scale" not in bands[0]:
        raise ValueError(
            f"asset {name!r} carries no raster:bands scale/offset; refusing to "
            "assume reflectance scaling. A normalised difference of raw DN "
            "looks valid but is not."
        )
    band = bands[0]
    return (float(band["scale"]),
            float(band.get("offset", 0.0)),
            band.get("nodata"))


def search_scenes(bbox: list[float], end: str, lookback_days: int,
                  max_scene_cloud: float) -> list:
    """Candidate scenes in [end - lookback_days, end], most recent first."""
    stop = date.fromisoformat(end)
    start = stop - timedelta(days=lookback_days)

    search = _client().search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{start.isoformat()}/{stop.isoformat()}",
        limit=100,
    )
    items = list(search.items())
    items = [i for i in items
             if (i.properties.get("eo:cloud_cover") or 0) <= max_scene_cloud]
    items.sort(key=lambda i: i.properties["datetime"], reverse=True)
    return items


class BelowValidFraction(ValueError):
    """Scenes covered the AOI, but none was clear enough at the threshold.

    The one Sentinel-2 failure that the WorldCover fallback answers
    Typed, so the fallback is chosen by what went wrong
    and not by matching the words of an error message. A coverage or tiling
    failure is a different problem and stays a plain ValueError. A subclass of
    ValueError, so every existing caller still sees one.
    """

    def __init__(self, message: str, attempts: list, *,
                 min_valid_fraction: float, best: dict | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.min_valid_fraction = min_valid_fraction
        self.best = best


class NoUsableMosaic(ValueError):
    """No same-date group of tiles formed one grid covering the AOI, even
    after tiles from a second UTM zone were dropped. The tiling failure the
    WorldCover fallback also answers (user decision 2026-10-27): it is raised
    only once the one-zone attempt has been made, so it is never a shortcut
    past a mosaic that could have been built."""

    def __init__(self, message: str, attempts: list):
        super().__init__(message)
        self.attempts = attempts


class NoCoveringScene(ValueError):
    """No single scene covers the AOI, but a mosaic of several might.

    A subclass of ValueError, so every existing caller and test that expects
    one still sees one. It exists so `fetch_lulc_data` can tell "nothing was
    clear enough" from "nothing was BIG enough" without matching on the text of
    an error message -- the first is a threshold the user owns, the second is a
    tile boundary the code can work around.
    """

    def __init__(self, message: str, attempts: list):
        super().__init__(message)
        self.attempts = attempts


def _utm_zone(item) -> str | None:
    """The scene's UTM zone number, from its own metadata, or None."""
    zone = item.properties.get("mgrs:utm_zone")
    if zone is not None:
        return str(int(zone))
    epsg = item.properties.get("proj:epsg")
    if epsg and str(epsg)[:3] in ("326", "327"):
        return str(int(str(epsg)[3:]))
    code = item.properties.get("grid:code") or ""
    digits = "".join(ch for ch in code.removeprefix("MGRS-")[:2] if ch.isdigit())
    return str(int(digits)) if digits else None


def _one_zone(members: list, bbox: list[float]) -> tuple[list, Any, list[str], str]:
    """For a date group whose tiles are not one grid -- an AOI next to a UTM
    zone boundary, where the neighbouring zone's tiles overlap it -- the
    tiles of ONE zone, if they alone plan a grid. Zones are tried by how many
    of the group's tiles they hold; the first whose tiles cover the whole AOI
    wins, else the one covering most. Returns (kept members, plan, dropped
    tile ids, the zone kept); raises ValueError when no single zone plans."""
    zones: dict[str | None, list] = {}
    for m in members:
        zones.setdefault(_utm_zone(m), []).append(m)
    if len(zones) < 2 or None in zones:
        raise ValueError("the tiles do not come from two identifiable UTM zones")
    best = None
    for zone, kept in sorted(zones.items(), key=lambda kv: -len(kv[1])):
        try:
            plan = R.plan_mosaic([m.assets["scl"].href for m in kept], bbox)
        except ValueError:
            continue
        if best is None or plan.coverage > best[1].coverage:
            best = (kept, plan, zone)
        if plan.coverage >= 1.0:
            break
    if best is None:
        raise ValueError("no single UTM zone's tiles form one grid either")
    kept, plan, zone = best
    dropped = [_tile_key(m) or m.id for m in members if m not in kept]
    return kept, plan, dropped, zone


def _tile_key(item) -> str | None:
    """MGRS tile identity, or None when the catalogue does not state it.

    Sentinel-2 products sit on a fixed worldwide MGRS grid, so every scene of
    one tile shares an extent, a CRS and a pixel alignment -- which makes the
    AOI window identical across dates. This key is what lets that window be
    computed once instead of once per candidate.

    None is returned rather than a guess: an unidentifiable scene must fall
    back to computing its own grid, never borrow another scene's.
    """
    code = item.properties.get("grid:code")
    if code:
        return str(code)
    zone = item.properties.get("mgrs:utm_zone")
    band = item.properties.get("mgrs:latitude_band")
    square = item.properties.get("mgrs:grid_square")
    if zone is not None and band and square:
        return f"MGRS-{zone}{band}{square}"
    return None


def _valid_fraction(classes: np.ndarray, keep: np.ndarray | None) -> float:
    """Share of the reporting domain that is real surface observation.

    `keep` is the land mask on the plan's full-resolution grid, or None to
    count every pixel in the window. Until 2026-09-26 selection always counted
    the whole window while the reported figure counted land only, so the sea
    -- usually clear -- was voting on whether a scene passed: Bangkok was
    selected on 0.8686 and reported 0.8243 of its land. The threshold and the
    reported figure now share one denominator.

    A decimated probe is smaller than the mask, so the mask is sampled at the
    probe's pixel centres. That makes the probe an estimate of a land fraction,
    which is all a probe ever was; the confirmation read uses the mask as is.
    """
    observed = np.isin(classes, list(SCL_VALID))
    if keep is None:
        return round(float(observed.mean()), 4)
    if keep.shape != classes.shape:
        rows = np.minimum(((np.arange(classes.shape[0]) + 0.5)
                           * keep.shape[0] / classes.shape[0]).astype(int),
                          keep.shape[0] - 1)
        cols = np.minimum(((np.arange(classes.shape[1]) + 0.5)
                           * keep.shape[1] / classes.shape[1]).astype(int),
                          keep.shape[1] - 1)
        keep = keep[np.ix_(rows, cols)]
    land = int(keep.sum())
    if land == 0:
        # Land smaller than one probe pixel. Not a clear scene -- an
        # unmeasurable one; probe_decimation=1 is the way round it.
        return 0.0
    return round(float((observed & keep).sum()) / land, 4)


def select_scene(items: list, bbox: list[float], min_valid_fraction: float,
                 max_probes: int, min_aoi_coverage: float = 1.0,
                 probe_decimation: int = 8,
                 land: Callable[[R.GridPlan], np.ndarray] | None = None,
                 ) -> tuple[Any, R.GridPlan, np.ndarray, list]:
    """Newest scene whose AOI is sufficiently clear. Returns its SCL too.

    Probing costs one windowed SCL read per candidate, so it is capped. Every
    rejection is recorded and returned -- provenance for why an older scene was
    used, which is exactly the sort of thing Entro needs to see.

    Probes read SCL through the COG's overviews at 1/`probe_decimation`, since
    a cloud fraction over a whole AOI does not need 20 m pixels: measured 5.1x
    faster with a worst-case error of 0.008 over six live scenes. The estimate
    only ever decides whether a scene is WORTH confirming -- the accepted scene
    is re-read at full resolution, so every number that leaves this function is
    exact. Set to 1 to probe at full resolution throughout.

    A scene that does not cover the whole AOI is rejected before it is read,
    the same way fetch_terrain_data and fetch_permanent_water refuse a bbox
    spanning two tiles. A Sentinel-2 AOI can straddle two MGRS tiles, and the
    window would otherwise be clamped to the part inside this scene -- yielding
    statistics for a smaller region than was asked for, with nothing in the
    response to say so. Rejection rather than a hard raise, because a scene
    from an adjacent orbit may well cover the AOI in full.

    `land(plan)` returns the reporting-domain mask for a grid -- the land, with
    the sea excluded -- and the valid fraction is taken over it, so the
    threshold judges the same ground the output describes. None counts the
    whole window; `fetch_lulc_data` always passes one unless nothing is
    excluded. It is called once per probed grid, so it should cache -- the
    one in `fetch_lulc_data` does, keyed on the grid itself.
    """
    attempts: list[dict[str, Any]] = []
    # One grid per MGRS tile, not one per scene. Candidates in a lookback
    # window are overwhelmingly the same tile on different dates -- 13 of 13
    # over Shillong -- and each plan_grid costs a COG open purely to re-derive
    # a window that is already known.
    plans: dict[str, R.GridPlan] = {}
    of = "land" if land is not None else "window"

    for item in items[:max_probes]:
        scl_href = item.assets["scl"].href
        tile = _tile_key(item)
        try:
            plan = plans.get(tile) if tile is not None else None
            if plan is None:
                # Cache miss, or a scene the catalogue did not identify: this
                # candidate gets its own grid. An AOI near a tile boundary
                # genuinely returns scenes from several tiles, and reusing a
                # neighbour's window there would read the wrong pixels.
                plan = R.plan_grid(scl_href, bbox)
                if tile is not None:
                    plans[tile] = plan
        except Exception as exc:                              # noqa: BLE001
            attempts.append({"scene_id": item.id,
                             "datetime": item.properties["datetime"],
                             "rejected": f"unreadable: {exc}"})
            continue

        if plan.coverage < min_aoi_coverage:
            attempts.append({
                "scene_id": item.id,
                "datetime": item.properties["datetime"],
                "scene_cloud_cover": item.properties.get("eo:cloud_cover"),
                "aoi_coverage": round(plan.coverage, 4),
                "rejected": (f"scene covers only {plan.coverage:.4f} of the "
                             f"AOI, below minimum {min_aoi_coverage}"),
            })
            continue

        try:
            probe = R.read_classes(scl_href, plan, decimation=probe_decimation)
        except Exception as exc:                              # noqa: BLE001
            attempts.append({"scene_id": item.id,
                             "datetime": item.properties["datetime"],
                             "rejected": f"unreadable: {exc}"})
            continue

        # Outside the try above on purpose: a land mask that cannot be built
        # is not a property of this scene, and swallowing it as "unreadable"
        # would quietly fall back to judging the sea.
        keep = land(plan) if land is not None else None

        estimate = _valid_fraction(probe, keep)
        record = {
            "scene_id": item.id,
            "datetime": item.properties["datetime"],
            "scene_cloud_cover": item.properties.get("eo:cloud_cover"),
            "aoi_coverage": round(plan.coverage, 4),
            "aoi_valid_fraction": estimate,
            "valid_fraction_is_of": of,
            "estimated": probe_decimation > 1,
            "probe_decimation": probe_decimation,
        }

        if estimate < min_valid_fraction:
            record["rejected"] = (f"AOI valid fraction {estimate} below "
                                  f"minimum {min_valid_fraction}")
            attempts.append(record)
            continue

        # Promising at 1/N. Confirm at full resolution before accepting, so the
        # returned valid_fraction, scl_counts and index statistics are measured
        # rather than estimated.
        try:
            scl = R.read_classes(scl_href, plan)
        except Exception as exc:                              # noqa: BLE001
            record["rejected"] = f"unreadable at full resolution: {exc}"
            attempts.append(record)
            continue

        fraction = _valid_fraction(scl, keep)
        record["probe_valid_fraction"] = estimate
        record["aoi_valid_fraction"] = fraction
        record["estimated"] = False

        if fraction >= min_valid_fraction:
            record["accepted"] = True
            attempts.append(record)
            return item, plan, scl, attempts

        # The estimate was optimistic by less than the decimation error. The
        # scene is still rejected -- the threshold is on the measurement, not
        # on the estimate -- and the near miss is recorded so it is not
        # mistaken for a probe that never looked.
        record["rejected"] = (
            f"AOI valid fraction {fraction} below minimum "
            f"{min_valid_fraction} on full-resolution confirmation "
            f"(1/{probe_decimation} probe estimated {estimate})")
        attempts.append(record)

    # Distinguish the two ways to get here. "Everything was cloudy" and "no
    # scene covered the AOI" call for different fixes, and reporting the first
    # when the second happened sends the caller after the wrong parameter.
    partial = [a for a in attempts if "aoi_coverage" in a
               and "aoi_valid_fraction" not in a]
    if partial and len(partial) == len(attempts):
        widest = max(partial, key=lambda a: a["aoi_coverage"])
        raise NoCoveringScene(
            f"no Sentinel-2 scene covers the whole AOI within the lookback "
            f"window ({len(attempts)} probed; widest was {widest['scene_id']} "
            f"at {widest['aoi_coverage']} of the AOI). The AOI probably "
            f"straddles two MGRS tiles -- mosaic it, split it, or lower "
            f"min_aoi_coverage to accept a partial scene.",
            attempts,
        )

    best = max((a for a in attempts if "aoi_valid_fraction" in a),
               key=lambda a: a["aoi_valid_fraction"], default=None)
    detail = (f"best was {best['scene_id']} at {best['aoi_valid_fraction']} "
              f"on {best['datetime'][:10]}") if best else "no scenes returned"
    partial_note = (f" {len(partial)} further scene(s) were rejected for "
                    f"partial AOI coverage.") if partial else ""
    message = (
        f"no Sentinel-2 scene reaches valid fraction {min_valid_fraction} over "
        f"the AOI within the lookback window ({len(attempts)} probed; "
        f"{detail}). The target ({MIN_VALID_FRACTION}) and the lookback "
        f"({LOOKBACK_DAYS} days) are fixed; the clearest scene is used instead."
        f"{partial_note}"
    )
    if best is None:
        raise ValueError(message)
    raise BelowValidFraction(message, attempts,
                             min_valid_fraction=min_valid_fraction, best=best)


def select_mosaic(items: list, bbox: list[float], min_valid_fraction: float,
                  max_probes: int, min_aoi_coverage: float = 1.0,
                  probe_decimation: int = 8,
                  land: Callable[[R.GridPlan], np.ndarray] | None = None,
                  ) -> tuple[list, R.GridPlan, np.ndarray, list]:
    """Newest set of same-date scenes that together cover the AOI clearly.

    The fallback for an AOI no single scene contains. Sentinel-2's MGRS tiles
    are 110 km and a river basin is not obliged to fit inside one: the Kelani
    crosses the T44NLN/T44NMN boundary, so every individual scene covers part
    of it and none covers all of it.

    **Scenes are grouped by acquisition DATE, and the group is the unit of
    selection.** One tile per group -- a date that returns the same tile twice
    keeps the first, because the second is a reprocessing, not a neighbour.
    Groups are tried newest first, exactly as single scenes are, because the
    point of this workflow is the most recent pre-event baseline.

    **A mosaic is not one observation and must not be reported as one.** The
    scenes in a group were acquired at different instants -- seconds apart when
    they share a datatake, hours when they do not -- under different sun angles
    and, at a swath edge, different view angles. `acquisition_span_seconds`
    travels with the result so a reader can see which case they have.

    Groups of one are skipped: `select_scene` has already measured every
    individual scene against these same two gates, and re-probing it here would
    spend a read to learn nothing.

    `land` is as in `select_scene`: the valid fraction is taken over the land
    it returns.
    """
    # Newest first, and one entry per tile within a date.
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        day = item.properties["datetime"][:10]
        tile = _tile_key(item) or item.id
        groups.setdefault(day, {}).setdefault(tile, item)

    of = "land" if land is not None else "window"
    attempts: list[dict[str, Any]] = []
    skipped = 0
    for day in sorted(groups, reverse=True):
        members = list(groups[day].values())
        if len(members) < 2:
            skipped += 1
            continue
        if len(attempts) >= max_probes:
            break

        stamps = sorted(m.properties["datetime"] for m in members)
        record: dict[str, Any] = {
            "date": day,
            "tiles": [_tile_key(m) or m.id for m in members],
            "scene_ids": [m.id for m in members],
            "scene_cloud_cover": [m.properties.get("eo:cloud_cover")
                                  for m in members],
            "acquisition_span_seconds": _span_seconds(stamps),
            "datatakes": sorted({d for d in (_datatake(m) for m in members)
                                 if d}),
        }

        hrefs = [m.assets["scl"].href for m in members]
        try:
            plan = R.plan_mosaic(hrefs, bbox)
        except ValueError as exc:                             # noqa: PERF203
            # Almost always an AOI next to a UTM zone boundary, where the other
            # zone's tiles overlap it. Pasting across zones would be a
            # reprojection, so instead the other zone's tiles are dropped when
            # one zone's tiles plan a grid on their own (user decision
            # 2026-10-27) -- the coverage gate below still decides whether
            # they are enough. What was dropped is recorded.
            try:
                members, plan, dropped, zone = _one_zone(members, bbox)
            except ValueError as why:
                record["rejected"] = f"tiles are not one grid: {exc}; {why}"
                attempts.append(record)
                continue
            hrefs = [m.assets["scl"].href for m in members]
            record.update({"tiles": [_tile_key(m) or m.id for m in members],
                           "scene_ids": [m.id for m in members],
                           "scene_cloud_cover": [m.properties.get("eo:cloud_cover")
                                                 for m in members],
                           "acquisition_span_seconds": _span_seconds(
                               sorted(m.properties["datetime"] for m in members)),
                           "datatakes": sorted({d for d in (_datatake(m) for m in members)
                                                if d}),
                           "utm_zone_kept": zone, "dropped_tiles": dropped,
                           "dropped_why": f"tiles from another UTM zone than {zone}; "
                                          "they cannot share one grid with it"})

        try:
            probe = R.read_classes_mosaic(hrefs, plan,
                                          decimation=probe_decimation)
        except Exception as exc:                              # noqa: BLE001
            record["rejected"] = f"unreadable: {exc}"
            attempts.append(record)
            continue

        record["aoi_coverage"] = round(plan.coverage, 4)
        record["estimated"] = probe_decimation > 1
        record["probe_decimation"] = probe_decimation
        if plan.coverage < min_aoi_coverage:
            record["rejected"] = (
                f"the {len(members)} tiles cover only {plan.coverage:.4f} of "
                f"the AOI between them, below minimum {min_aoi_coverage}")
            attempts.append(record)
            continue

        keep = land(plan) if land is not None else None

        estimate = _valid_fraction(probe, keep)
        record["aoi_valid_fraction"] = estimate
        record["valid_fraction_is_of"] = of
        if estimate < min_valid_fraction:
            record["rejected"] = (f"AOI valid fraction {estimate} below "
                                  f"minimum {min_valid_fraction}")
            attempts.append(record)
            continue

        # Promising at 1/N. A decimated mosaic places each tile to the nearest
        # decimated pixel, so its coverage figure is an estimate in a way a
        # single scene's never was -- confirm the whole thing at full
        # resolution before anything here is believed.
        try:
            scl = R.read_classes_mosaic(hrefs, plan)
        except Exception as exc:                              # noqa: BLE001
            record["rejected"] = f"unreadable at full resolution: {exc}"
            attempts.append(record)
            continue

        fraction = _valid_fraction(scl, keep)
        record["probe_valid_fraction"] = estimate
        record["probe_aoi_coverage"] = record["aoi_coverage"]
        record["aoi_coverage"] = round(plan.coverage, 4)
        record["aoi_valid_fraction"] = fraction
        record["estimated"] = False

        if plan.coverage < min_aoi_coverage:
            record["rejected"] = (
                f"the tiles cover {plan.coverage:.4f} of the AOI on "
                f"full-resolution confirmation, below minimum "
                f"{min_aoi_coverage}")
            attempts.append(record)
            continue

        if fraction >= min_valid_fraction:
            record["accepted"] = True
            attempts.append(record)
            return members, plan, scl, attempts

        record["rejected"] = (
            f"AOI valid fraction {fraction} below minimum "
            f"{min_valid_fraction} on full-resolution confirmation "
            f"(1/{probe_decimation} probe estimated {estimate})")
        attempts.append(record)

    if not attempts:
        raise ValueError(
            f"no date in the lookback window returned scenes from more than "
            f"one MGRS tile, so there is nothing to mosaic ({skipped} "
            f"single-tile date(s) seen, all already rejected individually). "
            f"The AOI cannot be covered from this catalogue within the window."
        )

    best = max((a for a in attempts if "aoi_valid_fraction" in a),
               key=lambda a: a["aoi_valid_fraction"], default=None)
    detail = (f"best was {best['date']} at coverage {best.get('aoi_coverage')} "
              f"and valid fraction {best['aoi_valid_fraction']}"
              if best else "none of them formed a readable grid")
    message = (
        f"no multi-tile mosaic reaches coverage {min_aoi_coverage} and valid "
        f"fraction {min_valid_fraction} over the AOI within the lookback "
        f"window ({len(attempts)} group(s) probed; {detail}). The valid "
        f"fraction and the lookback are fixed; lower min_aoi_coverage only "
        f"deliberately."
    )
    # A group that reached full coverage and was measured failed on cloud; none
    # reaching it is a tiling problem. It is typed, and the WorldCover fallback
    # answers it too -- raised only after the one-zone attempt above.
    if best is None:
        raise NoUsableMosaic(message, attempts)
    raise BelowValidFraction(message, attempts,
                             min_valid_fraction=min_valid_fraction, best=best)


def _span_seconds(stamps: list[str]) -> float:
    """Seconds between the first and last acquisition in a group.

    A magnitude, not a verdict. **Do not read a zero span as "one pass" or a
    non-zero one as "two passes"** -- Sentinel-2 tile products are cut out of a
    continuous datatake and each carries the timestamp of its own cut, so two
    tiles of a single pass are seconds apart as a matter of course.
    `_datatake` is what actually answers that question; this only says how far
    apart the cuts were.
    """
    from datetime import datetime

    if len(stamps) < 2:
        return 0.0
    moments = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in stamps]
    return round((max(moments) - min(moments)).total_seconds(), 1)


def _datatake(item) -> str | None:
    """The datatake a scene was cut from, or None if the catalogue omits it.

    One datatake is one continuous acquisition by one satellite on one orbit.
    Scenes sharing it are the same pass cut at a tile boundary, which is the
    difference between a mosaic that is a single observation and one that
    staples two together -- and it is not inferable from the timestamps.

    None rather than a guess: "the catalogue did not say" and "they differ" are
    different facts, and only the second is a caveat about the data.
    """
    value = item.properties.get("s2:datatake_id")
    return str(value) if value else None


def _read_band(members: list, name: str, plan: R.GridPlan,
               mosaicked: bool) -> np.ndarray:
    """One band onto `plan`'s grid, from a single scene or from a mosaic.

    Scaling is read per scene, never shared. Two scenes of one mosaic can carry
    different processing baselines, and the BOA offset does not cancel out of a
    normalised difference -- applying one scene's offset to the other's pixels
    would bias half the AOI by an amount no range check would catch.
    """
    if not mosaicked:
        asset = members[0].assets[name]
        scale, offset, nodata = _asset_scaling(asset, name)
        return R.read_band(asset.href, plan, scale=scale, offset=offset,
                           nodata=nodata)

    hrefs, per_tile = [], []
    for item in members:
        if name not in item.assets:
            raise ValueError(
                f"scene {item.id} carries no {name!r} asset, so this mosaic "
                "cannot be completed. A band present over part of an AOI and "
                "absent over the rest is not a band.")
        asset = item.assets[name]
        scale, offset, nodata = _asset_scaling(asset, f"{name} of {item.id}")
        hrefs.append(asset.href)
        per_tile.append({"scale": scale, "offset": offset, "nodata": nodata})

    # `plan.coverage` means the share of the AOI the TILES reached, measured
    # once from SCL. read_band_mosaic recomputes it as the share of finite
    # pixels in whatever band it just read, which for a band full of legitimate
    # nodata is a different and much lower number -- and it would be written
    # into every raster's metadata as though it were the AOI coverage.
    footprint = plan.coverage
    arr = R.read_band_mosaic(hrefs, plan, per_tile=per_tile)
    plan.coverage = footprint
    return arr


# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------
def _normalised_difference(a: np.ndarray, b: np.ndarray, *,
                           min_denominator: float = 1e-9) -> np.ndarray:
    """(a - b) / (a + b), with a small denominator as NaN rather than inf.

    `min_denominator` is a floor in reflectance units. For a plain band pair
    the default is enough -- the sum of two reflectances is only near zero
    where both are, and the result is bounded to [-1, 1] regardless. NMDI is
    the exception: one of its terms is itself a difference, so its denominator
    can pass through zero on real data and the ratio is then unbounded.
    """
    total = a + b
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(np.abs(total) > min_denominator, (a - b) / total, np.nan)
    return out.astype("float32")


def compute_indices(bands: dict[str, np.ndarray],
                    wanted: list[str]) -> dict[str, np.ndarray]:
    """Every requested index from the already-masked reflectance bands."""
    out: dict[str, np.ndarray] = {}

    if "ndvi" in wanted or "bui" in wanted:
        out["ndvi"] = _normalised_difference(bands["nir"], bands["red"])
    if "ndbi" in wanted or "bui" in wanted:
        out["ndbi"] = _normalised_difference(bands["swir16"], bands["nir08"])
    if "bui" in wanted:
        # NDBI alone flags anything bright in SWIR -- concrete, but also bare
        # soil, sand and dry riverbed. Subtracting NDVI removes the vegetation
        # half of that confusion and isolates built-up surface.
        #
        # TODO(arid-bui): it does not remove the bare-soil half, so over arid
        # ground BUI reports built-up surface that is not there. The direction
        # for this is unsettled -- see docs/arid_region.md before building
        # anything that consumes the built-up fraction as an estimate.
        out["bui"] = (out["ndbi"] - out["ndvi"]).astype("float32")
    if "ndmi" in wanted:
        out["ndmi"] = _normalised_difference(bands["nir"], bands["swir16"])
    if "mndwi" in wanted:
        out["mndwi"] = _normalised_difference(bands["green"], bands["swir16"])
    if "nmdi" in wanted:
        # NMDI uses the SWIR *difference* as one term, so it is genuinely
        # independent of NDMI rather than its algebraic negative. That is why
        # it is the better spatial soil-moisture proxy of the two.
        #
        # NMDI = (NIR - g) / (NIR + g), where g = SWIR16 - SWIR22. Unlike a
        # plain band pair this is bounded to [-1, 1] only while g > 0. Where
        # SWIR22 exceeds SWIR16 the denominator shrinks below the numerator and
        # the ratio diverges without bound.
        #
        # g > 0 is also the index's physical premise: Wang & Qu define it for
        # soil and canopy, where the 1.6 um band is brighter than the 2.2 um
        # band. Water, shadow and snow break that and are not surfaces NMDI
        # describes. So the mask is on g itself rather than on the denominator,
        # which bounds the result by construction instead of by a tuned cutoff.
        swir_gap = bands["swir16"] - bands["swir22"]
        swir_gap = np.where(swir_gap > NMDI_MIN_SWIR_GAP, swir_gap, np.nan)
        out["nmdi"] = _normalised_difference(bands["nir08"], swir_gap)

    return {k: v for k, v in out.items() if k in wanted}


def classify_ndvi(ndvi: np.ndarray) -> np.ndarray:
    """Provisional greenness classes. See NDVI_BINS -- these are not land cover."""
    codes = np.zeros(ndvi.shape, dtype="uint8")     # 0 = unclassified
    for low, high, code, _ in NDVI_BINS:
        codes[np.isfinite(ndvi) & (ndvi >= low) & (ndvi < high)] = code
    return codes


# ---------------------------------------------------------------------------
# The registered workflow
# ---------------------------------------------------------------------------
def _scene_block(members: list, valid: np.ndarray, start_date: str,
                 mosaicked: bool, domain: int | None = None) -> dict[str, Any]:
    """What was actually observed, named honestly.

    A mosaic gets `is_mosaic: true`, every member listed, and the span between
    the first and last acquisition. It does NOT get a single `datetime`
    pretending to be the moment of observation: `datetime` is the OLDEST
    member, because the baseline is only as recent as its stalest part, and
    `datetime_range` carries both ends.
    """
    stamps = sorted(m.properties["datetime"] for m in members)
    oldest = stamps[0]

    block: dict[str, Any] = {
        "id": " + ".join(sorted(m.id for m in members)),
        "is_mosaic": mosaicked,
        "datetime": oldest,
        "days_before_start": (date.fromisoformat(start_date)
                              - date.fromisoformat(oldest[:10])).days,
        # A share of the LAND, not of the bounding box. `valid.mean()` over the
        # whole array would fold the sea into the cloud figure and report a
        # coastal AOI as permanently overcast.
        "aoi_valid_fraction": round(
            float(valid.sum()) / max(1, domain if domain else valid.size), 4),
        "valid_fraction_is_of": "land_area_km2",
    }

    if not mosaicked:
        one = members[0]
        block["scene_cloud_cover"] = one.properties.get("eo:cloud_cover")
        block["platform"] = one.properties.get("platform")
        block["processing_baseline"] = one.properties.get(
            "s2:processing_baseline")
        return block

    span = _span_seconds(stamps)
    takes = [_datatake(m) for m in members]
    one_pass = len(set(takes)) == 1 and takes[0] is not None

    block["datetime_range"] = [stamps[0], stamps[-1]]
    block["acquisition_span_seconds"] = span
    block["datatakes"] = sorted({t for t in takes if t})
    block["single_datatake"] = one_pass

    if one_pass:
        block["acquisition_note"] = (
            f"One datatake ({takes[0]}): the members are the same pass of the "
            f"same satellite, cut at a tile boundary. The {span:.1f} s between "
            "their timestamps is the gap between those cuts, NOT two separate "
            "observations -- every tile product carries the time of its own "
            "cut. Treat this mosaic as a single observation. Across-track view "
            "angle still varies between tile columns, as it does across any "
            "one scene.")
    elif any(t is None for t in takes):
        block["acquisition_note"] = (
            f"The catalogue did not state a datatake for every member, so "
            f"whether they are one pass cannot be established from the "
            f"metadata. Their timestamps span {span:.1f} s. Do not assume "
            "either way.")
    else:
        block["acquisition_note"] = (
            f"{len(block['datatakes'])} separate datatakes, {span:.1f} s "
            "apart, so this is several observations pasted together. Sun "
            "angle, view angle and any surface change between them vary "
            "ACROSS the AOI rather than within one scene.")

    block["members"] = [{
        "id": m.id,
        "tile": _tile_key(m),
        "datetime": m.properties["datetime"],
        "datatake": _datatake(m),
        "relative_orbit": m.properties.get("sat:relative_orbit"),
        "scene_cloud_cover": m.properties.get("eo:cloud_cover"),
        "platform": m.properties.get("platform"),
        "processing_baseline": m.properties.get("s2:processing_baseline"),
    } for m in sorted(members, key=lambda m: m.id)]
    return block


def step_down(select: Any, min_valid_fraction: float,
              step_down_to: tuple[float, ...] | list[float]) -> tuple[Any, float, list[float]]:
    """Run `select(threshold)` at `min_valid_fraction`, then at each lower
    step, until one succeeds (user decision 2026-10-27). Only a cloud failure
    (BelowValidFraction) moves to the next step; any other failure is raised
    at once. Returns (the result, with every attempt at every threshold
    labelled with it, the threshold used, the thresholds tried). When the last
    step fails too, BelowValidFraction carries every attempt."""
    thresholds = [float(min_valid_fraction)] + [
        float(t) for t in step_down_to if float(t) < float(min_valid_fraction)]
    tried: list[float] = []
    earlier: list[dict[str, Any]] = []
    for threshold in thresholds:
        tried.append(threshold)
        try:
            members, plan, scl, attempts, mosaicked = select(threshold)
        except BelowValidFraction as exc:
            earlier += [dict(a, threshold=threshold) for a in exc.attempts]
            if threshold == thresholds[-1]:
                raise BelowValidFraction(
                    f"{exc} Thresholds tried, in order: {tried}.", earlier,
                    min_valid_fraction=threshold, best=exc.best) from None
            continue
        attempts = earlier + [dict(a, threshold=threshold) for a in attempts]
        return (members, plan, scl, attempts, mosaicked), threshold, tried
    raise AssertionError("unreachable: the loop returns or raises")


@workflow(
    "fetch_lulc_data",
    "Sentinel-2 land-cover indices for a bounding box, from the most recent "
    "cloud-free scene within a lookback window ending at start_date. "
    "`indices` takes any of NDVI, NDBI, BUI, NDMI, NMDI, MNDWI in any case; "
    "omit it for all six. Writes COGs and returns statistics.",
    label="Land-cover indices",
    source="Sentinel-2 L2A",
    data_source_uncertainty=(
        "What: optical imagery, so cloud and shadow hide land, the SCL cloud mask "
        "misclassifies some pixels, and the usable scene can be weeks older than "
        "the event. Affects: every land-cover share feeding the ranking; the "
        "indices are proxies too -- BUI > 0.2 gives 0.19 % built-up where "
        "WorldCover gives 17 % (BUG-010). Reduce: radar (Sentinel-1), multi-scene "
        "composites, or a check against WorldCover."),
    method_uncertainty=(
        "What: the most recent scene whose clear land reaches the 0.7 target; when "
        "none does within 90 days, the clearest scene, with its clear fraction "
        "reported as uncertainty (user decision 2026-11-11). Statistics cover the "
        "clear part only. Affects: 30 % or more of the land may be unobserved, and cloud gathers "
        "over high ground, so this is a bias, not random noise. Reduce: report the "
        "valid fraction and the threshold used beside each share (done), and prefer "
        "recent clear scenes."),
)
def fetch_lulc_data(
    bbox: list[float],
    start_date: str,
    end_date: str | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    min_valid_fraction: float = MIN_VALID_FRACTION,
    step_down_to: tuple[float, ...] | list[float] = STEP_DOWN_FRACTIONS,
    max_scene_cloud: float = 99.0,
    max_probes: int = 12,
    min_aoi_coverage: float = 1.0,
    mosaic: bool = True,
    exclude_water: tuple[str, ...] | list[str] = landmask.DEFAULT_EXCLUDE,
    probe_decimation: int = 8,
    indices: list[str] | None = None,
    output_dir: str = "outputs",
    event_id: int | str | None = None,
    write_rasters: bool = True,
) -> dict[str, Any]:
    """Land-cover indices for an AOI, from the newest usable pre-event scene.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        start_date: "YYYY-MM-DD". The lookback window ENDS here -- this is the
            pre-event baseline, so no imagery from during the event is used.
        end_date: accepted and echoed for symmetry with fetch_weather_data;
            it does not widen the search.
        lookback_days: how far back to walk for a usable scene. FIXED at 90
            (user decision 2026-11-01): any other value is replaced by 90 and
            kept in `selection.threshold_overridden`.
        min_valid_fraction: share of AOI pixels that must be real surface
            observation (not cloud/shadow) for a scene to be accepted.
            FIXED at 0.7 (user decision 2026-11-01): any other value is
            replaced by 0.7 and the value sent is kept in
            `selection.threshold_overridden`. The parameter stays so a call
            that names it still binds. See MIN_VALID_FRACTION above for why it is not
            the 0.9 the de-spiking convention suggests (docs/SOURCES.md
            section 6). Cloud is not randomly distributed: it concentrates
            over high ground, so the hidden pixels are systematically unlike
            the visible ones and the gap is bias, not noise.
        step_down_to: OFF (user decision 2026-11-01). Accepted so a call
            that names it still binds, and ignored: no lower threshold is
            tried. A list sent is kept in `selection.threshold_overridden`.
        max_scene_cloud: cheap tile-level prefilter before any pixel is read.
        max_probes: cap on SCL reads, so a fully clouded season stays bounded.
        min_aoi_coverage: share of the AOI a scene must actually cover to be
            accepted. Default 1.0 -- the whole of it. A Sentinel-2 AOI can
            straddle two MGRS tiles, and a partial scene yields statistics for
            a smaller region than was requested. Lower this only deliberately,
            and read `aoi.coverage` in the response when you do.
        exclude_water: Water Body Mask classes removed before any statistic is
            computed -- `("ocean",)` by default. A coastal bbox contains sea,
            and open water has an NDVI near zero, a saturated MNDWI and no land
            cover at all, so leaving it in drags every composition figure
            toward values that describe the Indian Ocean. Lakes and rivers are
            KEPT: they are part of the catchment. Pass `()` to keep everything.
        mosaic: when no single scene covers the AOI, paste same-date scenes
            from the neighbouring MGRS tiles into one grid instead of failing.
            Default True. It is a FALLBACK, never a first choice: a single
            scene is one acquisition and a mosaic is several, so the single
            scene is preferred whenever one exists. False restores the old
            behaviour of raising.
        probe_decimation: scene probes read SCL at 1/N resolution through the
            COG's overviews. The accepted scene is always re-read at full
            resolution, so this trades probe cost, not output accuracy. 1
            disables it.
        indices: subset of ndvi, ndbi, bui, ndmi, nmdi, mndwi, in any case.
            None means all six.
        output_dir, event_id: where COGs are written -- <output_dir>/<event_id>.
        write_rasters: False computes statistics without writing any file.

    Returns the selected scene, per-index statistics and raster paths, the
    SCL mask breakdown, and the full record of scenes that were rejected.
    """
    # 0.7 throughout, no step-down, 90 days back (user decisions 2026-11-01).
    # The bugs that prompted them: Monty sent min_valid_fraction=0.8, then
    # lookback_days=30, for Bangkok. A value a
    # model picks is not a decision anyone made, so it is replaced, and what
    # was sent is recorded on the result rather than silently dropped.
    overridden: dict[str, Any] = {}
    if float(min_valid_fraction) != MIN_VALID_FRACTION:
        overridden["min_valid_fraction"] = min_valid_fraction
        min_valid_fraction = MIN_VALID_FRACTION
    if list(step_down_to) != list(STEP_DOWN_FRACTIONS):
        overridden["step_down_to"] = list(step_down_to)
        step_down_to = STEP_DOWN_FRACTIONS
    if int(lookback_days) != LOOKBACK_DAYS:
        overridden["lookback_days"] = lookback_days
        lookback_days = LOOKBACK_DAYS

    # Case-folded, because NDVI and ndvi are the same index and only one of
    # them is how anyone writes it. This function's own registered description
    # -- the only text about it that reaches a prompt -- names them in capitals,
    # as do docs/SOURCES.md and the vault, so a caller that reads the catalogue
    # and passes them back verbatim was being rejected for agreeing with us.
    wanted = [str(i).strip().lower() for i in (indices or DEFAULT_INDICES)]
    unknown = [i for i in wanted if i not in _INDEX_BANDS]
    if unknown:
        raise ValueError(
            f"unknown index/indices {unknown}; expected any of "
            f"{sorted(_INDEX_BANDS)} (case does not matter)"
        )
    if len(bbox) != 4:
        raise ValueError(f"bbox must be [west, south, east, north], got {bbox}")

    candidates = search_scenes(bbox, start_date, lookback_days, max_scene_cloud)
    if not candidates:
        raise ValueError(
            f"no Sentinel-2 scenes at all over {bbox} in the {lookback_days} "
            f"days before {start_date}"
        )

    # The land mask is built during selection, so the threshold is judged on
    # the ground the output describes, and kept for the grid that wins.
    # Keyed on the grid, not on the plan object: every scene of one tile, and
    # every mosaic of one tile set, is the same grid and needs one mask read.
    # An `id()` key would be wrong the moment a freed plan's id was reused.
    censuses: dict[tuple, tuple[np.ndarray, dict[str, Any]]] = {}

    def grid_key(plan: R.GridPlan) -> tuple:
        return (str(plan.crs), tuple(plan.transform)[:6], tuple(plan.shape))

    def land_of(plan: R.GridPlan) -> np.ndarray:
        key = grid_key(plan)
        if key not in censuses:
            keep, report = landmask.land_mask(bbox, plan, exclude=exclude_water)
            if not keep.any():
                raise ValueError(
                    f"every pixel of {bbox} is excluded by exclude_water="
                    f"{list(landmask.classes(exclude_water))}; there is no land here to describe")
            censuses[key] = (keep, report)
        return censuses[key][0]

    # Nothing excluded means the whole window is the domain -- no mask to read.
    land_fn = land_of if landmask.classes(exclude_water) else None

    def select(threshold: float, pool: list | None = None) -> tuple[list, Any, np.ndarray, list, bool]:
        pool = candidates if pool is None else pool
        try:
            item, plan, scl, attempts = select_scene(
                pool, bbox, threshold, max_probes,
                min_aoi_coverage, probe_decimation, land=land_fn)
            return [item], plan, scl, attempts, False
        except NoCoveringScene as exc:
            # Every candidate was too SMALL, not too cloudy -- the AOI crosses
            # an MGRS boundary. Caught by type rather than by reading the
            # message, because the other failure ("nothing was clear enough")
            # is answered only by the threshold steps below.
            if not mosaic:
                raise
            try:
                members, plan, scl, mosaic_attempts = select_mosaic(
                    pool, bbox, threshold, max_probes,
                    min_aoi_coverage, probe_decimation, land=land_fn)
            except BelowValidFraction as inner:
                # Both records are kept, as on success.
                inner.attempts = list(exc.attempts) + inner.attempts
                raise
            # Both records are kept. The single-scene rejections are the
            # evidence that a mosaic was necessary, and dropping them would
            # leave the output looking like mosaicking was simply preferred.
            return members, plan, scl, list(exc.attempts) + mosaic_attempts, True

    # 0.7 is the TARGET, not a gate (user decision 2026-11-11). When no scene
    # reaches it, the clearest scene in the window is used and its clear
    # fraction is reported as uncertainty -- never hidden, never called a pass.
    best_available = None
    try:
        (members, plan, scl, attempts, mosaicked), used_fraction, tried = step_down(
            select, min_valid_fraction, step_down_to)
    except BelowValidFraction as exc:
        best = exc.best or {}
        if not best or not (best.get("aoi_valid_fraction") or 0) > 0:
            raise                         # no clear land at all: nothing to describe
        # The clearest candidate by name (one scene) or by date (a mosaic's
        # tiles), measured again at full resolution and accepted at what it
        # measures: a probe estimate never becomes the reported fraction.
        if best.get("scene_id"):
            pool = [i for i in candidates if i.id == best["scene_id"]]
        else:
            day = str(best.get("date", ""))[:10]
            pool = [i for i in candidates if str(i.properties.get("datetime", ""))[:10] == day]
        (members, plan, scl, again, mosaicked) = select(0.0, pool)
        attempts = [dict(a, threshold=float(min_valid_fraction)) for a in exc.attempts] + [
            dict(a, threshold="best available") for a in again]
        # The accepted attempt's own full-resolution measurement.
        used_fraction = next(a["aoi_valid_fraction"] for a in reversed(again) if a.get("accepted"))
        tried = [float(min_valid_fraction), "best available"]
        best_available = {
            "target_valid_fraction": float(min_valid_fraction),
            "valid_fraction": used_fraction,
            "unobserved_fraction": round(1 - float(used_fraction), 4),
            "note": (f"No scene in the {lookback_days} days before {start_date} reached the "
                     f"{min_valid_fraction} target; the clearest one was used. Every land-cover "
                     f"statistic describes the {used_fraction:.0%} of the land that was cloud-free, "
                     "and the rest is not observed -- cloud gathers over high ground, so this is a "
                     "bias, not random noise."),
        }
    item = members[0]

    # BUI needs NDVI and NDBI whether or not they were asked for.
    needed = set(wanted)
    if "bui" in needed:
        needed |= {"ndvi", "ndbi"}
    assets = {a for idx in needed for a in _INDEX_BANDS[idx]}

    # The reporting domain is the land inside the AOI. Two different things
    # blank a pixel from here on and they must not be conflated: cloud is an
    # OBSERVATION failure over ground that exists, and sea is ground that is
    # not part of the analysis at all. `valid` is the intersection; `land` is
    # the denominator.
    keep, land = (censuses.get(grid_key(plan))
                  or landmask.land_mask(bbox, plan, exclude=exclude_water))
    land_px = int(keep.sum())
    if land_px == 0:
        raise ValueError(
            f"every pixel of {bbox} is excluded by exclude_water="
            f"{list(landmask.classes(exclude_water))}; there is no land here to describe")

    observed = np.isin(scl, list(SCL_VALID))
    valid = observed & keep

    bands: dict[str, np.ndarray] = {}
    for name in sorted(assets):
        arr = _read_band(members, name, plan, mosaicked)
        arr[~valid] = np.nan          # mask before any arithmetic, not after
        # Surface reflectance outside [0, 1] is unphysical. It occurs anyway
        # over dark targets once the -0.1 BOA offset is applied, and a negative
        # reflectance propagates into index denominators as a sign flip. Clamp
        # rather than discard: the pixel is real, its value is just saturated.
        bands[name] = np.clip(arr, 0.0, 1.0)

    computed = compute_indices(bands, wanted)

    prefix = f"{output_dir}/{event_id or item.id}"
    results: dict[str, Any] = {}
    for name, arr in computed.items():
        value_range = INDEX_RANGES[name]
        entry: dict[str, Any] = {
            "range": list(value_range),
            # Every index here is bounded by construction, so the range is a
            # theoretical limit and pixels sitting on it are saturated rather
            # than extreme. That is worth counting -- see _raster.summarise.
            "stats": R.summarise(arr, value_range=value_range,
                                 saturating_at=value_range,
                                 domain=land_px),
        }
        if write_rasters:
            entry["raster"] = R.write_index(f"{prefix}/{name}.tif", arr, plan,
                                            value_range=value_range)
        results[name] = entry

    if "ndvi" in computed:
        codes = classify_ndvi(computed["ndvi"])
        legend = {code: label for _, _, code, label in NDVI_BINS}
        results["ndvi"]["classes"] = R.class_fractions(codes, legend, plan)
        results["ndvi"]["classes"]["caveat"] = (
            "Greenness bins, not land cover. NDVI does not separate forest, "
            "shrub and cropland."
        )
        if write_rasters:
            results["ndvi"]["classes"]["raster"] = R.write_classes(
                f"{prefix}/ndvi_class.tif", codes, plan)

    # Counted over the LAND only, so the cloud shares below are shares of the
    # ground being described rather than of a rectangle that includes the sea.
    scl_counts = {SCL_LABELS.get(int(c), str(int(c))): int(n)
                  for c, n in zip(*np.unique(scl[keep], return_counts=True))}

    return {
        "aoi": {
            # Both bboxes are reported, always. area_km2 is measured over
            # bbox_covered, so reading it without the coverage figure beside it
            # would describe a region nobody asked for.
            "bbox": bbox,
            "bbox_requested": bbox,
            "bbox_covered": plan.bounds_wgs84(),
            "coverage": round(plan.coverage, 4),
            # `area_km2` IS the land area, because that is what every figure in
            # this payload is a share of. The bounding box area sits beside it
            # under `land` so the two can never be read for one another.
            "area_km2": round(land_px * plan.pixel_area_km2, 4),
            "land_area_km2": round(land_px * plan.pixel_area_km2, 4),
            "bbox_area_km2": land["aoi_area_km2"],
            "land": land,
        },
        "scene": _scene_block(members, valid, start_date, mosaicked, land_px),
        "grid": plan.as_dict(),
        "indices": results,
        "scl_counts": scl_counts,
        "selection": {
            "lookback_days": lookback_days,
            "window": [
                (date.fromisoformat(start_date)
                 - timedelta(days=lookback_days)).isoformat(),
                start_date,
            ],
            "min_valid_fraction": used_fraction,
            "threshold_requested": float(min_valid_fraction),
            "thresholds_tried": tried,
            "threshold_stepped_down": float(used_fraction) < float(min_valid_fraction),
            # Set when no scene reached the target and the clearest was used:
            # the target, the measured fraction and the unobserved share.
            "best_available": best_available,
            # What a caller sent in place of the fixed threshold, replaced.
            "threshold_overridden": overridden,
            "min_aoi_coverage": min_aoi_coverage,
            "mosaic_allowed": mosaic,
            "mosaicked": mosaicked,
            "scenes_found": len(candidates),
            "scenes_probed": len(attempts),
            "attempts": attempts,
        },
        "period": {"start_date": start_date, "end_date": end_date},
        "source": {"catalogue": CATALOGUE_URL, "collection": COLLECTION},
    }
