"""AOI population from WorldPop Global2 (user decision, 2026-10-30).

One source, one method, for any country:

* **Source** -- WorldPop Global2 R2025A v1, constrained, 3 arc-seconds
  (about 100 m), people per pixel, one GeoTIFF per country and year
  (2015-2030). The file address is looked up through the WorldPop hub REST
  service (`G2_CN_POP_R25A_100m`), downloaded once and cached with its
  sidecar (`_fetch.cached_file`).
* **Method** -- the pixels whose CENTRE lies inside the bounding box are
  summed. Partial edge pixels are counted whole or not at all, so the edge
  band is reported as the range the total could move by.

This replaces the census / HDX COD-PS / World Bank chain and its centroid
allocation. National population as context still comes from
`fetch_world_bank_indicators`, and is never presented as the AOI's.

**Constrained** means people are placed only in grid squares WorldPop maps as
built settlement; everywhere else is no-data, which this tool counts as zero
people, not as missing. The sea holds no settlement, so it adds nothing and
no land mask is needed. R2025A is labelled an alpha release by WorldPop.

**People in the flood** (`extent_raster`, user decision 2026-11-02): the
stored flood extent is resampled onto the counted WorldPop window as the
SHARE of each pixel flooded and the share not observed, and the people of
each pixel are split by those shares -- WorldPop's ~100 m pixels and the
flood products' 20-375 m pixels never align, so a centre test would count a
pixel half under water as all or nothing. People on land the product did not
observe are reported apart, never as unaffected.

**One country per call.** The raster covers one country: the part of an AOI
outside it (another country, or sea beyond the raster's edge) is outside the
count, and `coverage.bbox_in_raster` says how much of the box that is.
"""

from __future__ import annotations

import math
from typing import Any

from . import tool
from . import _fetch as F

HUB_REST = "https://hub.worldpop.org/rest/data/pop/G2_CN_POP_R25A_100m?iso3="
DATASET_ID = "G2_CN_POP_R25A_100m"
RELEASE = "R2025A v1"
YEARS = range(2015, 2031)
DOI = "10.5258/SOTON/WP00839"


def _file_url(iso3: str, year: int) -> tuple[str, str]:
    """(GeoTIFF url, hub summary url) for one country and year, from the hub."""
    rows = (F.get_json(HUB_REST + iso3) or {}).get("data") or []
    for row in rows:
        if str(row.get("popyear")) == str(year):
            files = row.get("files") or []
            if files:
                return str(files[0]), str(row.get("url_summary") or "")
    raise ValueError(f"WorldPop lists no {RELEASE} constrained 100 m file for "
                     f"{iso3} {year} ({len(rows)} years listed)")


def _centre_window(transform, width: int, height: int,
                   bbox: list[float]) -> tuple[int, int, int, int, float]:
    """(row0, row1, col0, col1, share of the box inside the raster): the pixels
    whose centre lies inside the box, end-exclusive, clipped to the raster."""
    west, south, east, north = bbox
    x0, xres, y0, yres = transform.c, transform.a, transform.f, -transform.e
    col0 = math.ceil((west - x0) / xres - 0.5)
    col1 = math.floor((east - x0) / xres - 0.5) + 1
    row0 = math.ceil((y0 - north) / yres - 0.5)
    row1 = math.floor((y0 - south) / yres - 0.5) + 1
    want = max(col1 - col0, 0) * max(row1 - row0, 0)
    c0, c1 = max(col0, 0), min(col1, width)
    r0, r1 = max(row0, 0), min(row1, height)
    got = max(c1 - c0, 0) * max(r1 - r0, 0)
    return r0, r1, c0, c1, (got / want if want else 0.0)


def _in_extent(values, nodata, window_transform, crs, extent_raster: str) -> dict[str, Any]:
    """People in the flood and on unobserved land, from the extent's codes
    (1 flooded, 255 not observed) averaged onto the WorldPop window.

    The per-pixel shares travel under `_shares` for the map and are removed
    before the payload is returned (arrays never go into a return value)."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    v = values.astype("float64")
    bad = ~np.isfinite(v) | (v < 0)
    if nodata is not None:
        bad |= v == nodata
    people = np.where(bad, 0.0, v)
    with rasterio.open(extent_raster) as src:
        codes = src.read(1)
        src_t, src_crs = src.transform, src.crs
    shares = {}
    for key, mask in (("flooded", codes == 1), ("not_observed", codes == 255)):
        out = np.zeros(people.shape, dtype="float64")
        reproject(mask.astype("float64"), out, src_transform=src_t, src_crs=src_crs,
                  dst_transform=window_transform, dst_crs=crs,
                  resampling=Resampling.average, src_nodata=None, dst_nodata=None)
        shares[key] = np.clip(out, 0.0, 1.0)
    return {"people_in_flood": int(round(float((people * shares["flooded"]).sum()))),
            "people_not_observed": int(round(float((people * shares["not_observed"]).sum()))),
            "method": "each WorldPop pixel's people times the share of it the extent maps "
                      "flooded (average resampling of the extent onto the WorldPop grid)",
            "extent_raster": extent_raster, "_shares": shares}


def _sum(values, nodata) -> tuple[float, int, int]:
    """(people, pixels with people, no-data pixels)."""
    import numpy as np

    v = values.astype("float64")
    bad = ~np.isfinite(v) | (v < 0)
    if nodata is not None:
        bad |= v == nodata
    good = np.where(bad, 0.0, v)
    return float(good.sum()), int((good > 0).sum()), int(bad.sum())


def _draw_map(values, nodata, box: list[float], shares: dict | None, png,
              *, year: int, iso3: str, retrieved: str) -> None:
    """People per pixel over the AOI, with the flood extent on top.

    Drawn from the same window the total is summed over, so the map and the
    figure beside it cannot disagree. Log colour scale: settlement density
    spans four orders of magnitude and a linear ramp shows only the towns.
    Flooded share > 0 is red; land mostly unobserved (> 50 %) is hatched grey
    -- unknown, never dry.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LogNorm
    from matplotlib.patches import Patch

    from .. import dating

    v = values.astype("float64")
    bad = ~np.isfinite(v) | (v <= 0)
    if nodata is not None:
        bad |= v == nodata
    shown = np.ma.masked_where(bad, v)
    w, s, e, n = box
    extent = (w, e, s, n)
    from ..figures import _degree_ticks, finish_figure, styled_figure

    fig, ax = styled_figure(plt, figsize=(8, 8 * max(0.35, min(2.0, (n - s) / max(e - w, 1e-9)))),
                            dpi=150)
    top = float(shown.max()) if shown.count() else 1.0
    im = ax.imshow(shown, extent=extent, origin="upper", cmap="viridis",
                   norm=LogNorm(vmin=0.1, vmax=max(top, 1.0)), interpolation="nearest")
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    bar = fig.colorbar(im, cax=make_axes_locatable(ax).append_axes("right", size="3%", pad=0.12))
    bar.set_label("People per pixel (log scale)", fontsize=9)
    handles = [Patch(facecolor="white", edgecolor="0.6", label="No people mapped")]
    if shares:
        # Unobserved land as a LIGHT GREY overlay (user, 2026-11-19: the hatch
        # was misleading). Translucent, so the people under it still show;
        # the same grey as the HEV maps.
        unseen = shares["not_observed"] > 0.5
        if unseen.any():
            from matplotlib.colors import ListedColormap

            ax.imshow(np.ma.masked_where(~unseen, unseen.astype(float)), extent=extent,
                      origin="upper", cmap=ListedColormap(["#c8c8c8"]), alpha=0.6,
                      interpolation="nearest", vmin=0, vmax=1)
            # No share here: counted on this grid it would disagree with the
            # module's share of the land.
            handles.append(Patch(facecolor="#c8c8c8", alpha=0.6, edgecolor="0.5",
                                 label="Not observed (unknown, not dry)"))
        if (shares["flooded"] > 0).any():
            flooded = np.ma.masked_where(shares["flooded"] <= 0, shares["flooded"])
            ax.imshow(flooded, extent=extent, origin="upper",
                      cmap="Reds", vmin=0, vmax=1,
                      alpha=0.9, interpolation="nearest")
            handles.append(Patch(facecolor="#a50f15", label="Flood extent"))
        else:
            handles.append(Patch(facecolor="none", edgecolor="none",
                                 label="No flood mapped on observed land"))
    ax.legend(handles=handles, loc="lower left", framealpha=0.85)
    lons, lon_fmt = _degree_ticks(w, e)
    lats, lat_fmt = _degree_ticks(s, n)
    ax.set_xticks(lons)
    ax.set_xticklabels([lon_fmt.format(v) for v in lons])
    ax.set_yticks(lats)
    ax.set_yticklabels([lat_fmt.format(v) for v in lats])
    ax.set_xlabel("Longitude (\u00b0E)")
    ax.set_ylabel("Latitude (\u00b0N)")
    # The country by name, its code beside it (user, 2026-11-19: "what is LKA,
    # define it"). The name comes from the World Bank record; offline, the
    # code alone is printed rather than nothing.
    try:
        from .context import country_name

        country = f"{country_name(iso3)} ({iso3})"
    except Exception:                                          # noqa: BLE001
        country = iso3
    ax.set_title(f"Population, {country}, {year}", pad=10)
    zone = dating.zone_at(box)
    when = f"; file retrieved {dating.day(retrieved, zone)}" if retrieved else ""
    ax.annotate(f"Source: WorldPop Global2 {RELEASE} constrained, {year} -- a modelled "
                f"estimate, not a census{when}", xy=(0.5, 0.0), xycoords="axes fraction",
                xytext=(0, -46), textcoords="offset points", ha="center", va="top",
                fontsize=8, color="0.3", annotation_clip=False)
    finish_figure(plt, fig, png)


@tool(
    "get_pop_data",
    "Population inside a bounding box from WorldPop Global2 (R2025A v1, "
    "constrained, ~100 m, people per pixel), for one country and a year "
    "2015-2030 (default 2025). Sums the pixels whose centre lies inside the "
    "box and gives the edge band as a range. With `extent_raster` (get_flood_map's "
    "raster.path) it also gives the people inside the mapped flood extent, and "
    "apart the people on land the product did not observe.",
    label="Population",
    source="WorldPop Global2 R2025A",
    order=62,
    data_source_uncertainty=(
        "WorldPop Global2 R2025A is a modelled estimate, not a count: census totals "
        "are spread over settlement pixels by a random-forest model, and WorldPop "
        "labels this release alpha. Being constrained, it places people only where "
        "settlement is mapped, so missed settlements push people into neighbours."),
    method_uncertainty=(
        "The total sums the ~100 m pixels whose centre falls inside the bounding box, "
        "so the edge pixels can move it by the reported range. Only the named "
        "country's raster is read; any part of the box outside it is not counted."),
)
def get_pop_data(bbox: list[float], country_iso3: str,
                 year: int = 2025, extent_raster: str = "",
                 output_dir: str = "outputs", event_id: str = "",
                 write_figure: bool = True) -> dict[str, Any]:
    import rasterio
    from rasterio.windows import Window

    from .context import _iso3

    iso3 = _iso3(country_iso3)
    year = int(year)
    if year not in YEARS:
        raise ValueError(f"year must be {YEARS.start}-{YEARS.stop - 1}, got {year}")
    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    box = [float(v) for v in bbox]

    url, summary = _file_url(iso3, year)
    path, meta = F.cached_file(url, "worldpop_g2", url.rsplit("/", 1)[-1])

    with rasterio.open(path) as ds:
        r0, r1, c0, c1, share = _centre_window(ds.transform, ds.width, ds.height, box)
        if r1 <= r0 or c1 <= c0:
            raise ValueError(f"the bounding box {box} does not overlap the WorldPop "
                             f"raster of {iso3}")
        inner = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        # The ring one pixel outside the counted window: the most the total
        # could gain if the box edge were honoured exactly.
        e0, e1 = max(c0 - 1, 0), min(c1 + 1, ds.width)
        f0, f1 = max(r0 - 1, 0), min(r1 + 1, ds.height)
        outer = ds.read(1, window=Window(e0, f0, e1 - e0, f1 - f0))
        nodata, res = ds.nodata, abs(ds.transform.a)
        window_t = ds.window_transform(Window(c0, r0, c1 - c0, r1 - r0))
        crs = ds.crs

    people, with_people, nodata_px = _sum(inner, nodata)
    ring = _sum(outer, nodata)[0] - people
    # The inner edge: the outermost row/column of the counted window.
    import numpy as np

    edge_mask = np.zeros(inner.shape, dtype=bool)
    edge_mask[0, :] = edge_mask[-1, :] = edge_mask[:, 0] = edge_mask[:, -1] = True
    edge = _sum(np.where(edge_mask, inner, 0), nodata)[0]
    total = round(people)
    low, high = round(people - edge), round(people + ring)

    inputs = [
        F.item("modelled", f"WorldPop Global2 {RELEASE}: a random-forest dasymetric "
               "estimate, not a census count; WorldPop labels the release alpha.",
               RELEASE, "", "stated_by_source", str(year)),
        F.item("constrained", "Constrained: people are placed only in mapped settlement; "
               "no-data pixels are counted as zero people.", "constrained", "",
               "stated_by_source", str(year)),
    ]
    tool_items = [
        F.item("edge_band", f"Pixels are counted by their centre; the edge band moves the "
               f"total between {low:,} and {high:,}.", [low, high], "people", "measured"),
    ]
    if share < 0.9999:
        tool_items.append(F.item(
            "outside_raster", f"{(1 - share):.1%} of the box lies outside the {iso3} raster "
            "and is not counted.", round(1 - share, 4), "fraction of the box", "measured"))
    if meta.get("cached"):
        tool_items.append(F.item("cached_file", "The WorldPop file was read from the copy "
                                 f"retrieved {meta.get('retrieved_at')}.",
                                 meta.get("retrieved_at"), "", "measured"))

    flood: dict[str, Any] | None = None
    if extent_raster:
        flood = _in_extent(inner, nodata, window_t, crs, extent_raster)
        tool_items.append(F.item(
            "people_in_flood", f"People in the flood are WorldPop pixels split by the share of "
            f"each the extent maps flooded; {flood['people_not_observed']:,} more live on land "
            "the product did not observe and are not counted as unaffected.",
            flood["people_not_observed"], "people", "measured"))
    shares = flood.pop("_shares", None) if flood else None

    figure: dict[str, Any] = {}
    if write_figure:
        from pathlib import Path

        try:
            png = (Path(output_dir) / (str(event_id or "") or "population")
                   / "population_map.png")
            _draw_map(inner, nodata, box, shares, png, year=year, iso3=iso3,
                      retrieved=meta.get("retrieved_at", ""))
            figure = {"figure": {
                "name": "population_map", "path": str(png),
                "caption": (f"WorldPop Global2 {RELEASE} people per ~100 m pixel, {year} "
                            "(modelled)" + (", with the flood extent (red) and the land "
                                            "it did not observe (grey)" if shares else ""))}}
        except Exception as exc:                               # noqa: BLE001
            figure = {"map": {"ok": False, "problem": f"{type(exc).__name__}: {exc}"}}
    return {
        **figure,
        "country_iso3": iso3, "year": year, "bbox": box,
        **({"flood_extent": flood} if flood else {}),
        "population": total,
        "range": [low, high],
        "method": "sum of WorldPop pixels whose centre lies inside the bounding box",
        "vintage": f"WorldPop Global2 {RELEASE}, {year}",
        "dataset": {"id": DATASET_ID, "release": RELEASE, "constrained": True,
                    "resolution_deg": res, "units": "people per pixel"},
        "pixels": {"counted": int(inner.size), "with_people": with_people,
                   "no_data": nodata_px},
        "coverage": {"bbox_in_raster": round(share, 4)},
        "uncertainty": F.blocks(inputs, tool_items),
        "sources": [F.source(f"worldpop-g2-{iso3.lower()}-{year}",
                             f"WorldPop Global2 {RELEASE} constrained population, "
                             f"{iso3} {year}, 100 m", "WorldPop, University of Southampton",
                             summary or url, str(year), "CC BY 4.0",
                             retrieved_at=meta.get("retrieved_at", ""))],
    }
