"""Rendering a written raster into a figure a thesis can print.

The GeoTIFFs under `outputs/<event_id>/` are the data and are gitignored --
large, regenerable, and not readable by eye. These PNGs are the opposite: small,
committed beside the output that cites them, and the only form in which anyone
actually looks at an index.

**Every figure states its own stretch.** An index image is meaningless without
the range it was mapped over: NDVI drawn on [-1, 1] and the same array drawn on
[0.2, 0.9] are different pictures of identical data, and a reader cannot tell
them apart from the image alone. So the range is fixed per index from
`sentinel.INDEX_RANGES` rather than auto-scaled per scene, which also means two
events are comparable side by side.

Nodata is drawn as transparent, not as the bottom of the colour ramp. A cloud
gap rendered dark blue reads as water.

**Every figure carries the furniture a reader needs to use it without the
code**: a distance scale bar, a north arrow, and a legend entry naming the
blank pixels and what share of the AOI they are. A map without a scale is a
picture; the axes carry UTM eastings, which are correct and which nobody reads
as distance.

Categorical rasters go through `render_classes`, not `render`. A class code is
not a quantity: drawing codes 1-5 on a continuous ramp produces a smooth image
implying that "moderate vegetation" is midway between "bare" and "dense", and
interpolating across a class boundary invents a category. They get discrete
colours and a legend naming every class with its share and area.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Fixed display ranges. Auto-scaling would make every figure prettier and every
# comparison invalid.
RANGES: dict[str, tuple[float, float]] = {
    "ndvi": (-1.0, 1.0),
    "ndbi": (-1.0, 1.0),
    "ndmi": (-1.0, 1.0),
    "mndwi": (-1.0, 1.0),
    "nmdi": (-1.0, 1.0),
    "bui": (-2.0, 2.0),
    "slope": (0.0, 45.0),
    "elevation": None,          # genuinely scene-dependent; stretched per run
}

# What each index is for, printed under the figure so a reader does not have to
# hold six acronyms in their head.
CAPTIONS: dict[str, str] = {
    "ndvi": "NDVI -- greenness. High is dense vegetation, near zero is bare or built.",
    "ndbi": "NDBI -- built-up index. Cannot separate built-up from bare soil in arid terrain.",
    "bui": "BUI -- NDBI minus NDVI. Unvalidated over bare ground; see docs/arid_region.md.",
    "ndmi": "NDMI -- vegetation moisture.",
    "mndwi": "MNDWI -- open water. Event-time water extent, before the permanent baseline is subtracted.",
    "nmdi": "NMDI -- soil and vegetation moisture. Defined only where SWIR16 exceeds SWIR22.",
    "slope": "Slope in degrees, Horn's method on Copernicus GLO-30.",
    "elevation": "Elevation in metres, Copernicus GLO-30.",
}

# One text style for every raster figure (user, 2026-11-19: "make the font
# size and style consistent among all rasters"). Applied with rc_context in
# `render` and `render_classes`, so no figure sets its own sizes.
STYLE: dict[str, Any] = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 8,
    "legend.title_fontsize": 8.5,
}
FOOTNOTE_SIZE = 8          # every line under the map

# Titles in sentence case (user, 2026-11-19); acronyms stay upper case.
TITLES: dict[str, str] = {
    "ndvi": "NDVI", "ndbi": "NDBI", "bui": "BUI", "ndmi": "NDMI", "mndwi": "MNDWI",
    "nmdi": "NMDI", "slope": "Slope", "elevation": "Elevation",
    "ndvi_class": "NDVI classes", "slope_class": "Slope classes",
    "water_class": "Surface water classes (JRC)",
}

# The colour-bar label: what the colour measures, with its unit.
UNITS: dict[str, str] = {
    "elevation": "Elevation (m)", "slope": "Slope (degrees)",
    "ndvi": "NDVI", "ndbi": "NDBI", "bui": "BUI", "ndmi": "NDMI",
    "mndwi": "MNDWI", "nmdi": "NMDI",
}

# The class bins, printed in the legend (user, 2026-11-19: "the class bins
# should be explicitly mentioned in the raster"). Built from the same tables
# the workflows classify with, so the legend cannot disagree with the data.
# JRC water bins come from the payload's own thresholds (`bins` on the figure
# entry), because a call may override the defaults.
def _class_bins() -> dict[str, dict[int, str]]:
    from .workflows.sentinel import NDVI_BINS
    from .workflows.terrain import SLOPE_CLASSES

    ndvi = {}
    for low, high, code, _ in NDVI_BINS:
        ndvi[code] = (f"NDVI < {high:.2f}" if low < -1 else
                      f"NDVI ≥ {low:.2f}" if high > 1 else
                      f"NDVI {low:.2f} to {high:.2f}")
    slope = {code: (f"≥ {low:g}°" if high > 90 else f"{low:g} to {high:g}°")
             for low, high, code, _ in SLOPE_CLASSES}
    return {"ndvi_class": ndvi, "slope_class": slope}


def water_bins(permanent: float, seasonal: float) -> dict[int, str]:
    """JRC occurrence bins for the water_class legend, from the thresholds the
    workflow actually used."""
    return {1: f"occurrence > 0 to {seasonal:g}%",
            2: f"occurrence {seasonal:g} to {permanent:g}%",
            3: f"occurrence ≥ {permanent:g}%"}


# Where each classification's bins come from, printed under the class map
# (user, 2026-11-19: "cite the method used to classify the bins"). NDVI and
# slope have no paper recorded for their breaks; candidates are with the user
# for approval, and until then the figure says so rather than citing nothing.
CLASS_SOURCES: dict[str, str] = {
    "water_class": "Class breaks: Pekel et al. (2016), Nature 540, 418-422 "
                   "(permanent ≥ 90%, seasonal 25-90% occurrence).",
    "ndvi_class": "Class breaks: Aryal, Sitaula and Aryal (2022), Land 11, 351 -- NDVI "
                  "0.19, 0.40, 0.50 and 0.60 (their Level-3 thresholds); classes named "
                  "by greenness.",
    "slope_class": "Slope by Horn's (1981) method on Copernicus GLO-30; classes: flat "
                   "< 2°, gentle 2-5°, moderate 5-15°, steep 15-30°, "
                   "very steep ≥ 30°.",
}

CMAPS: dict[str, str] = {
    "ndvi": "RdYlGn", "ndmi": "BrBG", "mndwi": "RdBu", "nmdi": "BrBG",
    "ndbi": "cividis", "bui": "cividis",
    "slope": "magma", "elevation": "terrain",
}

# Discrete colours per categorical raster, keyed by class code.
#
# Chosen to mean something rather than to look pleasant: water is blue, built
# ground is grey-brown, vegetation runs pale to dark green with density, slope
# runs pale to dark with steepness. A reader who glances at the map and never
# reads the legend should still not be actively misled.
CLASS_COLOURS: dict[str, dict[int, str]] = {
    # NDVI greenness, Aryal et al. (2022) breaks (2026-11-19): grey-brown for
    # non-vegetation, then pale to dark green with greenness.
    "ndvi_class": {
        1: "#bda06b",     # non_vegetation      < 0.19
        2: "#d9ef8b",     # low_greenness       0.19-0.40
        3: "#a6d96a",     # moderate_greenness  0.40-0.50
        4: "#4daf4a",     # high_greenness      0.50-0.60
        5: "#1a6b32",     # very_high_greenness >= 0.60
    },
    "slope_class": {
        1: "#f7f7cd",     # flat        0-2
        2: "#fed976",     # gentle      2-5
        3: "#fd8d3c",     # moderate    5-15
        4: "#e31a1c",     # steep       15-30
        5: "#800026",     # very_steep  >= 30
    },
    "water_class": {
        1: "#c6dbef",     # ephemeral
        2: "#6baed6",     # seasonal
        3: "#08519c",     # permanent
    },
}

CLASS_CAPTIONS: dict[str, str] = {
    "ndvi_class": ("NDVI greenness bins -- NOT land cover. A single date cannot "
                   "separate forest, shrub and cropland; in the growing season a "
                   "closed canopy and a dense crop both sit near 0.8."),
    "slope_class": ("Slope classes from Copernicus GLO-30, a SURFACE model: "
                    "canopy and building heights inflate slope over vegetated "
                    "and built ground."),
    "water_class": ("JRC Global Surface Water occurrence classes, 1984-2021. A "
                    "long-run baseline, not the state on any single date."),
}

# What the blank pixels are, per raster family. Named in the legend rather than
# left white, because "no observation" and "zero" are different claims and a
# reader cannot tell them apart from the image.
# Excluded sea. Drawn, not left blank: a reader looking at a coastal figure
# needs to see WHERE the analysis stops, and an unexplained white margin is
# indistinguishable from a cloud bank. Pale enough to read as background rather
# than as a data class.
OCEAN_COLOUR = "#dbe9f6"
OCEAN_EDGE = "#9dbcd8"

NODATA_MEANS: dict[str, str] = {
    "ndvi": "cloud, shadow or cirrus",
    "ndbi": "cloud, shadow or cirrus",
    "bui": "cloud, shadow or cirrus",
    "ndmi": "cloud, shadow or cirrus",
    "nmdi": "cloud, shadow, cirrus or SWIR16 <= SWIR22",
    "mndwi": "cloud, shadow or cirrus",
    "ndvi_class": "cloud, shadow or cirrus",
    "slope": "outside the DEM tiles",
    "elevation": "outside the DEM tiles",
    "slope_class": "outside the DEM tiles",
    "water_class": "never water in the JRC record",
}


def _nice_length(target_m: float) -> float:
    """A round number of metres near `target_m`, for a scale bar.

    1, 2 or 5 times a power of ten. A bar labelled "13.7 km" is a bar nobody
    can use to measure anything by eye.
    """
    import math

    if target_m <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(target_m))
    for step in (1.0, 2.0, 5.0):
        if target_m < step * power * 1.5:
            return step * power
    return 10.0 * power


def _scale_bar(ax, *, geographic: bool, mid_lat: float) -> str:
    """A distance bar in the lower-left. Returns what it says, for the index.

    On a projected grid the axis units ARE metres, so the bar is exact. On a
    geographic one they are degrees, and a degree of longitude shortens with
    latitude -- so the bar is computed at the AOI's mid-latitude and labelled
    approximate. Drawing an exact-looking bar over a geographic grid would be
    the same class of error as labelling a UTM axis "longitude".
    """
    import math

    from matplotlib.patches import Rectangle

    left, right = ax.get_xlim()
    bottom, top = ax.get_ylim()
    width_data = abs(right - left)
    height_data = abs(top - bottom)

    if geographic:
        # Data units are degrees of longitude; how many metres is one?
        metres_per_unit = 111320.0 * math.cos(math.radians(mid_lat))
    else:
        metres_per_unit = 1.0

    target_m = width_data * metres_per_unit * 0.25
    bar_m = _nice_length(target_m)
    bar_data = bar_m / metres_per_unit

    x0 = left + width_data * 0.04
    y0 = bottom + height_data * 0.05
    thickness = height_data * 0.011

    # Two alternating segments, so the bar can be read as a ruler rather than
    # just a length.
    half = bar_data / 2.0
    for index, x in enumerate((x0, x0 + half)):
        ax.add_patch(Rectangle(
            (x, y0), half, thickness,
            facecolor="black" if index == 0 else "white",
            edgecolor="black", linewidth=0.6, zorder=6))

    # No "(approx.)" in the label (user, 2026-11-19). On a geographic grid the
    # bar is exact at the AOI's mid-latitude; over an AOI a few tens of km tall
    # the error is well under 1% -- recorded in the figure's metadata.
    label = (f"{bar_m / 1000:g} km" if bar_m >= 1000 else f"{bar_m:g} m")
    ax.text(x0 + bar_data / 2.0, y0 + thickness * 2.6, label,
            ha="center", va="bottom", fontsize=8, zorder=6,
            bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "none",
                  "pad": 1.2})
    return label


def _geographic_ticks(ax, crs, bounds, *, geographic: bool) -> str:
    """Label both axes in degrees, whatever the raster's own CRS is.

    **The pixels are not reprojected; only the ticks are.** Warping the image
    to EPSG:4326 to get degree labels would resample every value for the sake
    of an axis, and this module's whole job is to show what the data says. So
    the array stays on its native grid and the tick POSITIONS are converted:
    nice round degree values are chosen across the extent, then transformed
    into the raster's own coordinates to find where to put them.

    A graticule is curved in UTM, so a line of constant longitude is not a
    vertical line on this grid. The conversion is therefore done along the
    axis midline, where it is exact, and is accurate to well under a pixel
    across an AOI of this size -- at 7 degrees north, a 40 km wide box drifts
    by about 20 m between its middle and its edge. For a continent-sized
    extent this would not hold, which is why the axis label says which grid
    the image is actually on.

    Returns a short description of the grid, for the figure's own metadata.
    """
    lons_lats = _degree_ticks
    if geographic:
        lons, lon_fmt = lons_lats(bounds.left, bounds.right)
        lats, lat_fmt = lons_lats(bounds.bottom, bounds.top)
        ax.set_xticks(lons)
        ax.set_xticklabels([lon_fmt.format(v) for v in lons])
        ax.set_yticks(lats)
        ax.set_yticklabels([lat_fmt.format(v) for v in lats])
        ax.set_xlim(bounds.left, bounds.right)
        ax.set_ylim(bounds.bottom, bounds.top)
        ax.set_xlabel("Longitude (°E)")
        ax.set_ylabel("Latitude (°N)")
        return "EPSG:4326, axes are the data's own coordinates"

    from rasterio.warp import transform, transform_bounds

    west, south, east, north = transform_bounds(
        crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top)
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2

    lons, lon_fmt = lons_lats(west, east)
    lats, lat_fmt = lons_lats(south, north)

    # Each longitude placed at the AOI's middle latitude, and vice versa.
    xs, _ = transform("EPSG:4326", crs, lons, [mid_lat] * len(lons))
    _, ys = transform("EPSG:4326", crs, [mid_lon] * len(lats), lats)

    ax.set_xticks(xs)
    ax.set_xticklabels([lon_fmt.format(v) for v in lons])
    ax.set_yticks(ys)
    ax.set_yticklabels([lat_fmt.format(v) for v in lats])
    ax.set_xlim(bounds.left, bounds.right)
    ax.set_ylim(bounds.bottom, bounds.top)
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    return f"{crs}, ticks converted to degrees at the AOI midline"


def _degree_ticks(low: float, high: float) -> tuple[list[float], str]:
    """Round degree ticks across [low, high] and the one format they all share.

    Ticks closer than 6% of the span to either edge are dropped: the Kelani
    DEM's right-most label (80.150) sat on the frame and ran into the colour
    bar (user, 2026-11-19). Every label gets the same number of decimals, so
    a column reads 6.90 / 6.95 / 7.00, not 6.9 / 6.95 / 7.
    """
    import math

    from matplotlib.ticker import MaxNLocator

    span = high - low
    ticks = [v for v in MaxNLocator(nbins=5, steps=[1, 2, 2.5, 5, 10]).tick_values(low, high)
             if low + 0.06 * span <= v <= high - 0.06 * span]
    step = (ticks[1] - ticks[0]) if len(ticks) > 1 else span
    decimals = max(0, -math.floor(math.log10(step))) if step > 0 else 2
    if step > 0 and abs(step * 10 ** decimals - round(step * 10 ** decimals)) > 1e-6:
        decimals += 1                                  # 0.025 needs three
    return ticks, "{:." + str(decimals) + "f}"


def _north_arrow(ax, *, geographic: bool) -> None:
    """A north arrow in the upper-right.

    Labelled "N" on a geographic grid, where north is exactly up, and "GN"
    (grid north) on a projected one, where it is not. Over the Kelani AOI the
    UTM 44N convergence is about 0.12 degrees -- invisible on a printed page --
    but the distinction is free to state and wrong to assume in general.
    """
    # Wholly INSIDE the map (user, 2026-11-19: the "N" overflowed the frame):
    # placed in points from the top-right corner, so it stays inside whatever
    # the map's shape -- a wide, short AOI used to push the letter above it.
    from matplotlib.transforms import ScaledTranslation

    def inside(dx_pt: float, dy_pt: float):
        """The top-right corner of the map, moved by (dx, dy) points."""
        return ax.transAxes + ScaledTranslation(dx_pt / 72, dy_pt / 72, ax.figure.dpi_scale_trans)

    ax.annotate("", xy=(1.0, 1.0), xycoords=inside(-18, -24),
                xytext=(1.0, 1.0), textcoords=inside(-18, -46), zorder=6,
                arrowprops={"facecolor": "black", "edgecolor": "white", "width": 2.2,
                            "headwidth": 8.5, "headlength": 7.5, "linewidth": 0.5,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(1.0, 1.0, "N" if geographic else "GN", transform=inside(-18, -6),
            ha="center", va="top", fontsize=8, fontweight="bold", zorder=6,
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none", "pad": 1.0})


def render(tif: str | Path, out_png: str | Path, *, name: str,
           ocean=None, title: str = "", subtitle: str = "") -> dict[str, Any]:
    """One GeoTIFF -> one PNG with a colour bar, a title and its stretch.

    Returns what was drawn, including the range used, so the output document
    can state it rather than the reader having to trust the picture.
    """
    import matplotlib
    matplotlib.use("Agg")                       # no display on this machine
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio

    tif, out_png = Path(tif), Path(out_png)
    with rasterio.open(tif) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        bounds = src.bounds
        scale = (src.scales or [1.0])[0]
        # Axes are labelled from the raster's OWN crs, never assumed. The
        # Sentinel-2 products are EPSG:32644 -- UTM metres -- and labelling
        # those axes "longitude"/"latitude" put 400000 under a degree label on
        # every index figure. A reader who trusts the axis reads the AOI as
        # being off the planet.
        crs = src.crs
        geographic = bool(crs and crs.is_geographic)
    provenance = _provenance(tif)

    if nodata is not None:
        arr[arr == nodata] = np.nan
    # Indices are written as int16 scaled by 10000; undo that for display so
    # the colour bar reads in index units and not in thousands.
    if scale and scale != 1.0:
        arr = arr * scale

    finite = np.isfinite(arr)
    if not finite.any():
        raise ValueError(f"{tif} has no valid pixels to draw")

    span = RANGES.get(name)
    if span is None:
        lo, hi = (float(np.nanpercentile(arr, 2)),
                  float(np.nanpercentile(arr, 98)))
        stretch = f"2nd-98th percentile ({lo:.1f} to {hi:.1f})"
    else:
        lo, hi = span
        stretch = f"fixed {lo:g} to {hi:g}"

        # **A fixed stretch the data does not fit is a broken figure, not a
        # stylistic choice.** For a year `_raster._write` recorded an index's
        # scale in its return value but never in the file, so the descale above
        # was skipped and raw counts in the thousands were drawn against a
        # colour bar running -1 to 1. Every pixel clipped to the top of the
        # ramp, and the result was a flat block of colour that looked
        # deliberate.
        #
        # Refusing beats clamping: a figure is a deliverable, and a saturated
        # one cannot be told from a real one by looking -- which is the only
        # way anybody checks a picture. A percentile stretch needs no such
        # guard, because it is derived from the data and fits by construction.
        values = arr[finite]
        low, high = float(values.min()), float(values.max())
        outside = int(((values < lo) | (values > hi)).sum())
        if outside / max(1, values.size) > 0.01:
            raise ValueError(
                f"{tif.name}: {outside / values.size:.1%} of valid pixels fall "
                f"outside the fixed stretch [{lo:g}, {hi:g}] -- the data runs "
                f"[{low:.4g}, {high:.4g}]. Drawing this would saturate the "
                f"colour bar and hide it. If the raster is stored scaled, its "
                f"`scales` tag is missing; if the stretch is wrong, fix RANGES. "
                f"Do not render it as it is.")

    fig, ax = styled_figure(plt, figsize=(7.6, 7.2), dpi=150)
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]

    # Sea first, underneath, so the data layer draws over it and only the
    # excluded margin shows through.
    ocean_share = _draw_ocean(ax, ocean, arr.shape, extent)

    image = ax.imshow(
        np.ma.masked_invalid(arr), cmap=CMAPS.get(name, "viridis"),
        vmin=lo, vmax=hi, extent=extent)
    grid_note = _geographic_ticks(ax, crs, bounds, geographic=geographic)
    ax.set_title(title or TITLES.get(name, name.replace("_", " ").capitalize()), pad=10)

    # The colour bar is exactly as tall as the map: a wide, short AOI (the
    # Kelani basin) otherwise got a bar twice its height.
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    cax = make_axes_locatable(ax).append_axes("right", size="3%", pad=0.12)
    bar = fig.colorbar(image, cax=cax)
    bar.set_label(f"{UNITS.get(name, name.upper())}  [{stretch}]", fontsize=9)
    bar.ax.tick_params(labelsize=9)

    p2, p98 = (float(np.nanpercentile(arr, 2)), float(np.nanpercentile(arr, 98)))
    valid = float(finite.sum()) / arr.size

    # Map furniture: a distance the reader can measure by, a direction, and a
    # name for the blank pixels. The axes carry UTM eastings -- correct, and
    # not something anyone reads as distance.
    mid_lat = (bounds.bottom + bounds.top) / 2 if geographic else 0.0
    bar_label = _scale_bar(ax, geographic=geographic, mid_lat=mid_lat)
    _north_arrow(ax, geographic=geographic)

    # The cloud gap is a share of the LAND, which is what the statistics are
    # over -- not of the rectangle, which includes the sea.
    land_share = 1.0 - ocean_share
    cloud_share = (1.0 - valid / land_share) if land_share > 0 else 0.0
    _nodata_legend(ax, name, cloud_share, ocean_share)

    # The data's own spread, stated beside the fixed stretch rather than
    # inside the map. NDVI over a tropical basin sits at 0.67-0.88, so on a
    # fixed -1..1 ramp the image is nearly uniform -- a true picture of a
    # homogeneous AOI and an unreadable one. Printing both lets a reader see
    # that the flatness is the finding, not a rendering choice.
    # `valid` counts finite pixels over the WHOLE frame, sea included. The
    # figure's domain is the land, so it is restated against the land before
    # being printed -- otherwise the footer says 63.8% observed while the
    # legend beside it says 25.8% cloud, and both are computed correctly
    # against denominators nobody named.
    observed_of_land = (valid / land_share) if land_share > 0 else 0.0
    pixel_m = abs(bounds.right - bounds.left) / arr.shape[1]
    # The stretch / data p2-p98 / pixel / size line is no longer printed under
    # the map (user, 2026-11-19); the same facts stay in the returned metadata
    # and the colour bar still names its stretch.
    _caption_block(fig, subtitle, "", CAPTIONS.get(name), grid_note, provenance, ax=ax)

    finish_figure(plt, fig, out_png)

    return {
        "name": name,
        "png": str(out_png),
        "source_tif": str(tif),
        "provenance": provenance,
        "stretch": stretch,
        "vmin": lo,
        "vmax": hi,
        "valid_fraction": round(valid, 4),
        "crs": str(crs),
        "axis_units": "degrees",
        "grid_note": grid_note,
        "data_p2_p98": [round(p2, 4), round(p98, 4)],
        "pixel": f"{pixel_m:.4g} {'deg' if geographic else 'm'}",
        "shape": [int(arr.shape[0]), int(arr.shape[1])],
        "scale_bar": bar_label,
        "scale_bar_note": ("exact at the AOI mid-latitude; a geographic grid"
                           if geographic else "exact; a projected grid"),
        "north": "true north (up)" if geographic else "grid north (up)",
        "nodata_means": NODATA_MEANS.get(name, "no observation"),
        "ocean_fraction_of_frame": round(ocean_share, 4),
        "cloud_fraction_of_land": round(cloud_share, 4),
        "caption": CAPTIONS.get(name, ""),
        "categorical": False,
    }


def styled_figure(plt, **kwargs):
    """A figure in the shared STYLE; `finish_figure` saves it and restores
    matplotlib's own settings, so other charts in the process are untouched."""
    saved = {key: plt.rcParams[key] for key in STYLE}
    plt.rcParams.update(STYLE)
    fig, ax = plt.subplots(**kwargs)
    fig._pralai_rc = saved
    return fig, ax


def finish_figure(plt, fig, out_png: Path) -> None:
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(out_png, bbox_inches="tight", facecolor="white", pad_inches=0.3)
    finally:
        plt.close(fig)
        plt.rcParams.update(getattr(fig, "_pralai_rc", {}))


def _provenance(tif: Path) -> str:
    """The figure's "Source: ... | dated ..." line, from the GeoTIFF's own tags
    (BUG-020). A raster written before the tags existed says so, rather than
    the figure going out with no source and no date."""
    from .dating import TAG_DATED, TAG_SOURCE, figure_line, read_tags

    tags = read_tags(tif)
    if not tags:
        return ("Source and acquisition date: not recorded in this raster "
                "(written before BUG-020; re-run the tool to tag it)")
    return figure_line(tags.get(TAG_SOURCE, ""), tags.get(TAG_DATED, ""))


def _caption_block(fig, subtitle: str, footer: str, caption: str | None,
                   grid_note: str, provenance: str = "", ax=None) -> None:
    """Everything below the map, stacked with room to breathe.

    Anchored to the bottom of the MAP, in points, and stacked downwards line
    by line (2026-11-19). It used to sit at fixed figure fractions, which left
    a hand's width of blank page under any wide, short AOI (the Kelani basin)
    and collided as soon as a line wrapped. In points from the axes, every
    line follows the map whatever its shape, and `bbox_inches="tight"` keeps
    all of it.
    """
    import textwrap

    size = FOOTNOTE_SIZE
    lines = [(subtitle, "0.15", "normal"),
             (textwrap.fill(provenance, 120), "0.10", "normal"),
             (textwrap.fill(footer, 120), "0.15", "normal"),
             (f"Grid: {grid_note}", "0.45", "normal")]
    if caption:
        lines.append((textwrap.fill(caption, 120), "0.30", "italic"))
    lines = [item for item in lines if item[0]]
    if not lines:
        return
    ax = ax or fig.axes[0]
    offset = 46.0                       # below the tick labels and the axis label
    for text, colour, style in lines:
        ax.annotate(text, xy=(0.5, 0.0), xycoords="axes fraction",
                    xytext=(0, -offset), textcoords="offset points",
                    ha="center", va="top", fontsize=size, color=colour, style=style,
                    annotation_clip=False)
        offset += (size + 4.5) * (text.count("\n") + 1) + 3.0


def _draw_ocean(ax, ocean, shape, extent) -> float:
    """Shade the excluded sea and return what share of the frame it is.

    `ocean` is a boolean array on the figure's own grid, True where the sea
    was removed from the analysis. None means nothing was excluded, which is
    a different statement from "no sea here" only in that it is the caller's
    to make.
    """
    import numpy as np
    from matplotlib.colors import ListedColormap

    if ocean is None:
        return 0.0
    ocean = np.asarray(ocean, dtype=bool)
    if ocean.shape != tuple(shape):
        raise ValueError(
            f"the ocean mask is {ocean.shape} but the raster is {tuple(shape)}; "
            "a mask drawn on a different grid would shade the wrong ground")
    if not ocean.any():
        return 0.0
    ax.imshow(np.ma.masked_where(~ocean, ocean.astype("uint8")),
              cmap=ListedColormap([OCEAN_COLOUR]), vmin=0, vmax=1,
              extent=extent, interpolation="nearest", zorder=1)
    return float(ocean.mean())


def _nodata_legend(ax, name: str, cloud_share: float,
                   ocean_share: float = 0.0) -> None:
    """Name the blank pixels, and say how much of the map they are.

    Without this the white is just white, and a reader has to decide for
    themselves whether it means zero, water, or nothing was measured. Those are
    three different maps.

    **Cloud and sea get separate entries and separate denominators**, because
    they are separate facts. Cloud is an observation failure over ground that
    exists, and its share is of the LAND -- the ground the statistics describe.
    Sea is ground excluded from the analysis, and its share is of the frame.
    Adding them would produce a single "missing" figure that is a fraction of
    nothing in particular.
    """
    from matplotlib.patches import Patch

    handles = []
    if cloud_share > 0.0005:
        handles.append(Patch(
            facecolor="white", edgecolor="0.35", linewidth=0.6,
            label=f"No data: {NODATA_MEANS.get(name, 'not observed')}"
                  f"  ({cloud_share:.1%} of land)"))
    if ocean_share > 0.0005:
        handles.append(Patch(
            facecolor=OCEAN_COLOUR, edgecolor=OCEAN_EDGE, linewidth=0.6,
            label=f"Sea: excluded from all statistics  "
                  f"({ocean_share:.1%} of frame)"))
    if not handles:
        return
    ax.legend(handles=handles, loc="upper left", framealpha=0.85,
              borderpad=0.4, handlelength=1.4, handleheight=1.0)


def render_classes(tif: str | Path, out_png: str | Path, *, name: str,
                   legend: dict[str | int, str],
                   fractions: dict[str, float] | None = None,
                   areas: dict[str, float] | None = None,
                   share_of: str = "observed", ocean=None,
                   title: str = "", subtitle: str = "",
                   bins: dict[str | int, str] | None = None) -> dict[str, Any]:
    """One categorical GeoTIFF -> one PNG with a discrete legend.

    Separate from `render` because a class code is not a quantity. Put codes
    1-5 on a continuous ramp and the image says "moderate vegetation" is
    halfway between "bare" and "dense", and any interpolation across a boundary
    invents a category that is not in the data.

    `fractions` and `areas` come from the workflow's own `class_fractions`
    output and are printed in the legend. They are NOT recomputed here: a
    figure that derives its own numbers can disagree with the table beside it,
    and then there are two answers and no way to tell which the text cites.

    **`share_of` names the denominator, and it goes in the legend.** A
    percentage without one is the single most reliable way to be wrong in this
    project: NDVI classes are shares of the OBSERVED area, because cloud is
    excluded, while slope and JRC water are shares of the FULL AOI, because
    neither layer has a cloud mask. 62.5% of observed and 62.5% of the AOI are
    different claims about different amounts of ground, and a reader holding a
    printed figure has nothing else to go on.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    tif, out_png = Path(tif), Path(out_png)
    with rasterio.open(tif) as src:
        codes = src.read(1)
        nodata = src.nodata
        bounds = src.bounds
        crs = src.crs
        geographic = bool(crs and crs.is_geographic)
    provenance = _provenance(tif)

    # Legend keys arrive as strings from JSON and as ints from Python.
    table = {int(code): str(label) for code, label in legend.items()}
    if not table:
        raise ValueError(f"{tif.name}: a categorical figure needs a legend")

    palette = CLASS_COLOURS.get(name)
    if palette is None:
        ramp = plt.get_cmap("viridis", max(len(table), 2))
        palette = {code: ramp(i) for i, code in enumerate(sorted(table))}

    present = sorted(table)
    blank = 0 if nodata is None else int(nodata)
    masked = np.ma.masked_where(
        (codes == blank) | (~np.isin(codes, present)), codes)

    # One colour per code, in code order, with the boundaries between them at
    # the half-integers -- so a pixel of code 3 gets the third colour and
    # nothing is ever interpolated.
    colours = [palette[code] for code in present]
    cmap = ListedColormap(colours)
    norm = BoundaryNorm([present[0] - 0.5] + [c + 0.5 for c in present],
                        cmap.N)

    fig, ax = styled_figure(plt, figsize=(7.6, 7.2), dpi=150)
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    ocean_share = _draw_ocean(ax, ocean, codes.shape, extent)
    ax.imshow(masked, cmap=cmap, norm=norm, interpolation="nearest",
              extent=extent)
    grid_note = _geographic_ticks(ax, crs, bounds, geographic=geographic)
    ax.set_title(title or TITLES.get(name, name.replace("_", " ").capitalize()), pad=10)

    # Each class with its bin (user, 2026-11-19): "Dense vegetation
    # (NDVI >= 0.6) -- 52.4% of observed land, 39.5 km2".
    edges = {int(k): v for k, v in (bins or _class_bins().get(name) or {}).items()}
    handles = []
    for code in present:
        label = table[code].replace("_", " ").capitalize()
        if code in edges:
            label += f" ({edges[code]})"
        share = (fractions or {}).get(table[code])
        area = (areas or {}).get(table[code])
        if share is not None:
            label += f"  --  {share:.1%} of {share_of}"
        if area is not None:
            label += f", {area:,.1f} km\u00b2"
        handles.append(Patch(facecolor=palette[code], edgecolor="0.3",
                             linewidth=0.5, label=label))

    blank = (float(masked.mask.sum()) / masked.size
             if np.ma.is_masked(masked) else 0.0)
    # The sea is already counted in `blank` -- every excluded pixel is
    # unclassified -- so subtract it rather than reporting the same ground
    # twice under two names.
    blank_share = max(0.0, blank - ocean_share)
    if blank_share > 0.0005:
        handles.append(Patch(
            facecolor="white", edgecolor="0.35", linewidth=0.6,
            label=f"No data: {NODATA_MEANS.get(name, 'not observed')}"
                  f"  ({blank_share:.1%} of frame)"))
    if ocean_share > 0.0005:
        handles.append(Patch(
            facecolor=OCEAN_COLOUR, edgecolor=OCEAN_EDGE, linewidth=0.6,
            label=f"Sea: excluded from all statistics  "
                  f"({ocean_share:.1%} of frame)"))

    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.02, 0.5),
              frameon=True, framealpha=0.95, borderpad=0.6,
              title=f"Classes (shares of {share_of})")

    mid_lat = (bounds.bottom + bounds.top) / 2 if geographic else 0.0
    bar_label = _scale_bar(ax, geographic=geographic, mid_lat=mid_lat)
    _north_arrow(ax, geographic=geographic)

    caption = CLASS_CAPTIONS.get(name)
    _caption_block(fig, subtitle, CLASS_SOURCES.get(name, ""), caption, grid_note, provenance, ax=ax)

    finish_figure(plt, fig, out_png)

    return {
        "name": name,
        "png": str(out_png),
        "source_tif": str(tif),
        "provenance": provenance,
        "categorical": True,
        "classes": {str(code): table[code] for code in present},
        "fractions": fractions or {},
        "fractions_are_of": share_of,
        "area_km2": areas or {},
        "nodata_fraction": round(blank_share, 4),
        "ocean_fraction_of_frame": round(ocean_share, 4),
        "nodata_means": NODATA_MEANS.get(name, "no observation"),
        "crs": str(crs),
        "axis_units": "degrees",
        "grid_note": grid_note,
        "scale_bar": bar_label,
        "north": "true north (up)" if geographic else "grid north (up)",
        "caption": caption or "",
        "bins": {str(k): v for k, v in edges.items()},
        "class_source": CLASS_SOURCES.get(name, ""),
        "shape": [int(codes.shape[0]), int(codes.shape[1])],
    }
