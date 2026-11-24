"""Charts for the decision outputs -- drawn from a payload, never recomputed.

`core/figures.py` renders rasters: scale bars, north arrows, a stretch stated
on every image. None of that applies here. These are charts of numbers that
have already been computed and written down, and the one rule that matters is
that **a chart adds no analysis**. Every value drawn is read out of the cached
payload the corresponding output document was rendered from, so the picture
and the document cannot disagree. If a band edge here is wrong, it is wrong in
output 5 too, and that is the intended property.

The shared convention with `figures.py` is kept: axes are fixed rather than
auto-scaled, so two events are comparable side by side; the caption is baked
into the image, because a figure that travels into a slide without its caption
must still say what it is; and every figure gets a self-describing row in the
event's `figures/index.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import dating

# Colour-blind-safe, and deliberately two weights of one hue rather than two
# hues: the bands are nested, not categories. A reader should see "inside" and
# "outside", not "blue thing" and "orange thing".
LEADER_BAND = "#bcd4e8"
ORDER_BAND = "#2b5d8a"
MARK = "#c1440e"
UNREACHABLE_EDGE = "#8a8a8a"


def _band(change: dict[str, Any] | None, *, low: bool) -> tuple[float, bool]:
    """One edge of a stability band, and whether it is open.

    A direction with no threshold means the sweep reached the end of the range
    without the order changing. That is an **open** edge -- the band is bounded
    by the axis, not by a crossing -- and it is drawn differently, because
    "stable all the way to zero" and "stable down to 0.05" are different
    findings and a bare number cannot tell them apart.
    """
    if change is None:
        return (0.0 if low else 1.0), True
    return float(change["weight"]), False


def stability_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The bands, straight from the sensitivity payload. Nothing is recomputed.

    Raises if the order-stable band is not contained in the leader-stable one.
    That containment is what makes the nested drawing honest: every change of
    leader is also a change of order, so the inner band cannot escape the
    outer one. If it ever does, the region is not the single interval this
    figure assumes and the figure would be a lie rather than a simplification.
    """
    rows = []
    for entry in payload["criteria"]:
        order_lo, order_lo_open = _band(entry["first_change"]["down"], low=True)
        order_hi, order_hi_open = _band(entry["first_change"]["up"], low=False)
        lead_lo, lead_lo_open = _band(entry["leader_change"]["down"], low=True)
        lead_hi, lead_hi_open = _band(entry["leader_change"]["up"], low=False)

        if lead_lo > order_lo + 1e-9 or lead_hi < order_hi - 1e-9:
            raise ValueError(
                f"{entry['criterion']}: the order-stable band "
                f"[{order_lo:.6f}, {order_hi:.6f}] is not inside the "
                f"leader-stable band [{lead_lo:.6f}, {lead_hi:.6f}]. A nested "
                "drawing would misrepresent this; the region is not one "
                "interval.")

        rows.append({
            "criterion": entry["criterion"],
            "name": entry["name"],
            "weight": float(entry["weight"]),
            "reachable": bool(entry["reachable"]),
            "modulated_by": entry["modulated_by"],
            "order": (order_lo, order_hi, order_lo_open, order_hi_open),
            "leader": (lead_lo, lead_hi, lead_lo_open, lead_hi_open),
            "width": order_hi - order_lo,
        })
    # Most fragile first, so the eye lands on the weak link -- but a criterion
    # no base weight can reach is not the weak link, whatever its band width,
    # so the unreachable ones go last rather than heading a list a reader will
    # read as "fix this one". Ties broken by criterion id to keep the figure
    # byte-stable between runs.
    rows.sort(key=lambda r: (not r["reachable"], r["width"], r["criterion"]))
    return rows


def stability_bands(payload: dict[str, Any], out_png: str | Path, *,
                    event: str = "") -> dict[str, Any]:
    """Draw the weight-stability bands for one sensitivity payload.

    One row per criterion, on a fixed 0-1 weight axis. The light band is where
    the top-ranked alternative keeps its place; the dark band nested inside is
    where the whole order holds. The marker is the weight the event actually
    produced.

    The x2 and /2 ticks are a **reading aid, not a plausibility claim**.
    Nothing in this project sources a range of defensible weights, so the
    figure does not invent one; what the ticks do is stop a full 0-1 axis from
    making every band look reassuringly wide. Against them it is visible that
    some bands close before the weight has doubled and others run past it.
    """
    import matplotlib
    matplotlib.use("Agg")                        # no display on this machine
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle

    rows = stability_rows(payload)
    leader = payload["baseline"]["leader"]
    grid_step = payload["grid"]["grid_step"]

    # Explicit margins rather than `tight_layout` plus `bbox_inches="tight"`.
    # Those two fight: the first reserves space inside the canvas, the second
    # then crops to the artists, and the result was a figure with its rows
    # squashed into the top third and the rest blank. Fixed fractions also
    # keep the row pitch honest -- a two-line criterion label needs about
    # half an inch of row, and a layout engine that shrinks rows to fit more
    # of them produces overlapping labels rather than a smaller figure.
    row_pitch = 0.56
    top_margin, bottom_margin = 1.45, 1.55
    height = top_margin + bottom_margin + row_pitch * len(rows)
    fig, ax = plt.subplots(figsize=(11.5, height), dpi=200)
    fig.subplots_adjust(left=0.238, right=0.975,
                        top=1.0 - top_margin / height,
                        bottom=bottom_margin / height)

    for position, row in enumerate(rows):
        y = len(rows) - 1 - position
        lead_lo, lead_hi, _, _ = row["leader"]
        order_lo, order_hi, lo_open, hi_open = row["order"]

        ax.add_patch(Rectangle((lead_lo, y - 0.30), lead_hi - lead_lo, 0.60,
                               facecolor=LEADER_BAND, edgecolor="none",
                               zorder=2))
        # An unreachable band is hatched. The hatch takes its colour from the
        # EDGE colour, so `edgecolor="none"` draws no hatch at all however the
        # pattern is set -- which is how the first version of this figure
        # rendered W2 as an ordinary band.
        ax.add_patch(Rectangle(
            (order_lo, y - 0.19), max(order_hi - order_lo, 0.0015), 0.38,
            facecolor=ORDER_BAND, zorder=3,
            edgecolor="none" if row["reachable"] else "white",
            linewidth=0.0,
            hatch=None if row["reachable"] else "////"))

        # The weight the event produced.
        ax.plot([row["weight"]], [y], marker="D", markersize=5.0,
                color=MARK, zorder=6, clip_on=False)

        # x2 and /2 of that weight: a sense of scale, not a claim.
        for factor in (0.5, 2.0):
            tick = row["weight"] * factor
            if 0.0 < tick <= 1.0:
                ax.plot([tick, tick], [y - 0.33, y + 0.33], color="#4d4d4d",
                        linewidth=0.9, linestyle=(0, (2, 2)), zorder=5)

        # Threshold values at the edges that are real crossings; an arrow
        # where the band ran to the end of the range without the order
        # changing. The arrow sits at the TOP of the band rather than on its
        # centre line: a criterion whose weight is already near zero has its
        # event marker on the same few pixels -- L2 at 0.0117 drew its diamond
        # over its own open-edge caret -- and a marker parked outside the axis
        # instead lands in the gutter where the longest criterion names end.
        if lo_open:
            ax.plot([0.005], [y + 0.235], marker="<", markersize=4.2,
                    color="white", zorder=7)
        else:
            ax.text(order_lo - 0.008, y, f"{order_lo:.3f}", va="center",
                    ha="right", fontsize=7.2, color="#1a1a1a", zorder=7)
        if hi_open:
            ax.plot([0.995], [y + 0.235], marker=">", markersize=4.2,
                    color="white", zorder=7)
        else:
            ax.text(order_hi + 0.008, y, f"{order_hi:.3f}", va="center",
                    ha="left", fontsize=7.2, color="#1a1a1a", zorder=7)

        if not row["reachable"]:
            # Beside its own row, not in the margin: an aside floating at the
            # top of the figure attaches itself to the title instead of to the
            # criterion it is about.
            ax.text(min(lead_hi + 0.015, 0.62), y,
                    f"unreachable -- the {row['modulated_by']} modulator is 0 "
                    "at this event, so no base weight reaches this band",
                    va="center", ha="left", fontsize=6.9, style="italic",
                    color=UNREACHABLE_EDGE, zorder=7)

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(
        [f"{r['criterion']}  {r['name']}\nweight {r['weight']:.4f}"
         for r in reversed(rows)], fontsize=7.6)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0.0, 1.0)                        # fixed, never auto-scaled
    ax.set_xlabel("Criterion weight (the other eleven held in proportion)",
                  fontsize=8.5)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", color="#d8d8d8", linewidth=0.6, zorder=1)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    ax.set_title(
        f"How far one weight can move before the ranking changes"
        + (f"  --  {event}" if event else ""),
        fontsize=11, loc="left", pad=26, weight="bold")
    ax.text(0.0, 1.0, f"Wider is more robust. Sorted most fragile first, with "
                      f"criteria no weight can reach placed last. "
                      f"Top-ranked alternative: {leader}.",
            transform=ax.transAxes, va="bottom", ha="left", fontsize=8.2,
            color="#3a3a3a")

    fig.legend(handles=[
        Patch(facecolor=ORDER_BAND, label="the whole order holds"),
        Patch(facecolor=LEADER_BAND, label=f"{leader} keeps first place"),
        Line2D([0], [0], marker="D", color="none", markerfacecolor=MARK,
               markersize=5.5, label="weight at this event"),
        Line2D([0], [0], color="#4d4d4d", linewidth=0.9,
               linestyle=(0, (2, 2)), label="half / double that weight"),
    ], loc="lower left", bbox_to_anchor=(0.238, 0.70 / height),
        ncol=4, frameon=False, fontsize=8.0, handlelength=1.7,
        columnspacing=1.6)

    caption = (
        "Weights only: product scores, the contested evidence cells, the P2 "
        "range and the author parameters are held where output 3 records them."
        "\nThe full 0-1 range is swept and most of it is not an opinion anyone "
        "holds -- the half/double ticks are a sense of scale, not a range of "
        "defensible weights."
        "\nAn arrow at either end means the band ran to the end of the range "
        f"without the order changing.  Search grid {grid_step} in weight "
        "units; every other edge bisected."
        f"\n{dating.figure_line_for('sensitivity_analysis', payload, None)}")
    # Wrapped by hand. `wrap=True` measures against the figure width and
    # breaks mid-clause, which puts the caveat and its qualifier on different
    # lines -- and these caveats are the point of the caption.
    fig.text(0.238, 0.14 / height, caption, fontsize=6.9, color="#4a4a4a",
             va="bottom", linespacing=1.5)

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)

    return {
        "name": "sensitivity_stability",
        "png": str(out_png).replace("\\", "/"),
        "kind": "chart",
        "chart": "weight-stability bands",
        "source": "sensitivity_analysis payload (inputs/sensitivity.json)",
        "computed_here": "nothing -- every edge is read from the payload",
        "x_axis": "criterion weight, fixed 0 to 1",
        "sorted_by": "width of the order-stable band, narrowest first; "
                     "unreachable criteria last",
        "rows": [
            {"criterion": r["criterion"], "weight": round(r["weight"], 6),
             "order_band": [round(r["order"][0], 6), round(r["order"][1], 6)],
             "order_band_open": [r["order"][2], r["order"][3]],
             "leader_band": [round(r["leader"][0], 6),
                             round(r["leader"][1], 6)],
             "reachable": r["reachable"]}
            for r in rows],
        "marks": "half and double the event's weight; a reading aid, not a "
                 "plausibility claim",
        "grid_step": grid_step,
        "caption": caption,
        "categorical": False,
    }


# Availability chart. One hue for "observed", greys for the three ways a cell
# can be empty -- which are different claims and must not look alike.
AVAIL_FILL = "#2b5d8a"
AVAIL_NONE = "#9a9a9a"
AVAIL_UNCHECKED = "#e6e6e6"
AVAIL_NOT_PRODUCED = "#f2f2f2"
AVAIL_UNREACHABLE = "#c1440e"
CLOUD_TEXT = "#3a7bbf"


def availability(payload: dict[str, Any], out_png: str | Path) -> dict[str, Any]:
    """Draw the product-by-day availability grid from a `data_availability`
    payload.

    Rows are the five NRT products in the MCDM's order; columns are the days of
    the window, local to the AOI. A filled circle is one or more acquisitions,
    sized by the count and labelled with it. The three empty states are drawn
    apart because they mean different things: `0` is "searched, nothing",
    hatched grey is "the rolling archive no longer holds this day", pale grey
    is "the product did not exist yet". For the two daily optical products,
    the model cloud cover at their overpass is printed under every cell -- on a
    day that cannot be checked it is the only evidence there is.

    Text labels, not logos, for now (user decision 2026-10-11).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle

    import math
    import textwrap

    days = payload["period"]["days"]
    products = payload["products"]
    n = len(days)
    cell = min(0.46, 13.0 / max(n, 1))              # inches per day column
    left_in, right_in = 2.5, 1.35
    top_in = 0.6
    row_in = 0.62
    width = left_in + right_in + cell * n

    legend = [
        Patch(facecolor=AVAIL_FILL, label="acquisitions that day (number = count; "
                                          "MODIS/VIIRS from GIBS: 1 = the day's composite "
                                          "saw ground)"),
        Patch(facecolor="white", edgecolor=AVAIL_NONE,
              label="0 = searched, none (MODIS/VIIRS: or the composite saw only cloud)"),
        Patch(facecolor=AVAIL_UNCHECKED, edgecolor="white", hatch="////",
              label="no data = neither LANCE nor GIBS holds the day"),
        Patch(facecolor=AVAIL_NOT_PRODUCED, label="n/a = before the product existed"),
        Patch(facecolor="white", edgecolor=AVAIL_UNREACHABLE, hatch="xx",
              label="? = catalogue search failed"),
    ]

    cloud = payload.get("cloud") or {}
    cloud_line = (f"Blue % = {cloud.get('what', 'model cloud cover at overpass')}; lower "
                  f"of Terra/Aqua for MODIS. {cloud.get('model', '')}. "
                  if "what" in cloud else
                  f"Cloud cover not shown: {cloud.get('unavailable') or cloud.get('skipped', 'n/a')}. ")
    zone = (payload.get("period") or {}).get("timezone")
    dated = "; ".join(dating.lines("data_availability", payload, zone))
    retrieved = (f"; catalogues searched {dating.stamp(payload['retrieved_at'], zone)}"
                 if payload.get("retrieved_at") else "")
    gibs_line = (f"{payload['instance_definition_gibs']}. "
                 if payload.get("instance_definition_gibs") and any(
                     d.get("source") == "gibs" for p in products
                     for d in p["days"].values()) else "")
    caption = (f"Instance = {payload.get('instance_definition', '')}. {gibs_line}{cloud_line}"
               + dating.figure_line("EODC STAC (GFM); NASA CMR-STAC (OPERA DSWx, LANCE "
                                    "MCDWD/VCDWD); NASA GIBS (MCDWD/VCDWD daily "
                                    "composites); Open-Meteo (cloud)", dated + retrieved))

    # The space under the grid is measured, not guessed: the day labels, then
    # the legend in as many columns as the width holds, then the caption
    # wrapped to the width. A short window makes a narrow figure, and a fixed
    # band let a three-column legend run off the edge and the caption print
    # over it (Bangkok, 7 days, 2026-11-01).
    ncol = 3 if width >= 10.5 else 2 if width >= 7.0 else 1
    legend_in = math.ceil(len(legend) / ncol) * 0.19 + 0.08
    wrapped = "\n".join(textwrap.wrap(caption, width=max(40, int((width - 0.25) * 19))))
    caption_in = (wrapped.count("\n") + 1) * 0.115 + 0.05
    ticks_in, gap_in = 0.55, 0.12
    bottom_in = ticks_in + gap_in + legend_in + gap_in + caption_in + 0.1
    height = top_in + bottom_in + row_in * len(products)
    fig, ax = plt.subplots(figsize=(width, height), dpi=170)
    fig.subplots_adjust(left=left_in / width, right=1 - right_in / width,
                        top=1 - top_in / height, bottom=bottom_in / height)
    font = max(5.5, min(8.5, cell * 18))

    for row, product in enumerate(products):
        y = len(products) - 1 - row
        has_cloud = any("cloud_pct" in d for d in product["days"].values())
        for x, day in enumerate(days):
            entry = product["days"].get(day) or {"status": "none", "count": 0}
            status, count = entry["status"], entry.get("count", 0)
            if status == "not_checkable":
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor=AVAIL_UNCHECKED,
                                       edgecolor="white", hatch="////", linewidth=0.6))
                ax.text(x, y + 0.12, "no\ndata", ha="center", va="center",
                        fontsize=font * 0.7, color="#555555", linespacing=0.9)
            elif status == "in_gibs":
                # An OLDER payload's GIBS day (BUG-030), from before such days
                # were read and counted; a new payload marks them available.
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor=AVAIL_UNCHECKED,
                                       edgecolor="white", linewidth=0.6))
                ax.text(x, y + (0.08 if has_cloud else 0), "G", ha="center", va="center",
                        fontsize=font, color=AVAIL_FILL, fontweight="bold")
            elif status == "not_produced":
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1,
                                       facecolor=AVAIL_NOT_PRODUCED, edgecolor="white"))
                ax.text(x, y, "n/a", ha="center", va="center",
                        fontsize=font * 0.8, color="#888888")
            elif status == "unreachable":
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor="white",
                                       edgecolor=AVAIL_UNREACHABLE, hatch="xx",
                                       linewidth=0.4))
                ax.text(x, y, "?", ha="center", va="center", fontsize=font,
                        color=AVAIL_UNREACHABLE)
            elif count > 0:
                # A scatter marker, sized in points: a Circle patch is drawn in
                # data units and turns into an ellipse whenever a day column is
                # narrower than a product row is tall.
                size = (min(1.0, 0.55 + 0.15 * (count - 1)) * cell * 72 * 0.8) ** 2
                ax.scatter([x], [y + (0.08 if has_cloud else 0)], s=size,
                           color=AVAIL_FILL, edgecolors="none", zorder=3)
                ax.text(x, y + (0.08 if has_cloud else 0), str(count), ha="center",
                        va="center", fontsize=font, color="white", zorder=4,
                        fontweight="bold")
            else:
                ax.text(x, y + (0.08 if has_cloud else 0), "0", ha="center",
                        va="center", fontsize=font * 0.85, color=AVAIL_NONE)
            if has_cloud:
                cloud = entry.get("cloud_pct") or {}
                values = [v for v in cloud.values() if v is not None]
                ax.text(x, y - 0.30, f"{min(values):.0f}%" if values else "--",
                        ha="center", va="center", fontsize=font * 0.75,
                        color=CLOUD_TEXT)

        totals = product.get("totals") or {}
        gibs_days = totals.get("days_in_gibs", 0)
        old_gibs = sum(1 for d in product["days"].values() if d.get("status") == "in_gibs")
        checked = (n - totals.get("days_not_checkable", 0) - totals.get("days_not_produced", 0)
                   - old_gibs)
        if product.get("status") == "unreachable":
            summary = "search failed"
        elif old_gibs and checked == 0:
            summary = f"GIBS {old_gibs} d"
        elif checked == 0:
            # "0 in 0 d" would read as "no data"; the truth is "no record kept".
            summary = "not checkable"
        else:
            summary = (f"{totals.get('instances', 0)} in "
                       f"{totals.get('days_available', 0)} d"
                       + ("\n(GIBS)" if gibs_days and not old_gibs else ""))
        ax.text(n - 0.5 + 0.25, y, summary, ha="left", va="center", fontsize=8,
                color="#333333")

    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(-0.5, len(products) - 0.5)
    ax.set_yticks(range(len(products)))
    ax.set_yticklabels([f"{p['label']}\n({p['sensor']})" for p in reversed(products)],
                       fontsize=8.5)
    step = 1 if n <= 31 else 2
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([f"{d[8:10]}\n{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(d[5:7]) - 1]}"
                        for d in days[::step]], fontsize=max(6, font))
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for x in range(n + 1):
        ax.axvline(x - 0.5, color="#eeeeee", linewidth=0.5, zorder=0)
    for y in range(len(products) + 1):
        ax.axhline(y - 0.5, color="#dddddd", linewidth=0.6, zorder=0)

    period = payload["period"]
    offset = period.get("utc_offset_h", 0)
    zone = period.get("timezone") or "solar time"
    ax.set_title(f"NRT flood product availability over the AOI, "
                 f"{period['start']} to {period['end']}\n"
                 f"(local dates, {zone}, UTC{offset:+g})", fontsize=10.5, pad=10)

    # Legend directly under the day labels, caption under the legend; each
    # band was sized above, so neither can reach the other.
    fig.legend(handles=legend, loc="upper left", ncol=ncol, fontsize=7, frameon=False,
               borderaxespad=0, borderpad=0,
               bbox_to_anchor=(0.12 / width, (bottom_in - ticks_in - gap_in) / height))
    fig.text(0.12 / width, 0.1 / height, wrapped, fontsize=6.5, color="#444444",
             va="bottom", ha="left", linespacing=1.25)

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    return {"name": "data_availability", "png": str(out_png), "caption": caption,
            "days": n, "categorical": True}


HYETO_BAR = "#2b6cb0"
HYETO_CUM = "#c05621"
HYETO_BURST = "#bee3f8"
HYETO_GAP = "#bdbdbd"


def hyetograph(payload: dict[str, Any], out_png: str | Path) -> dict[str, Any]:
    """Draw the rainfall hyetograph of a `fetch_weather` payload.

    Bars are the AOI-mean rainfall per hour (`hourly.vars.precipitation.mean`),
    the line is the rain accumulated since the window opened, rain bursts are
    shaded and labelled with their depth, and the wettest 24 h is bracketed.
    Every value is read from the payload; nothing is recomputed except the
    running sum the line draws, which is the payload's own hourly means added
    up in order.

    **A missing hour is drawn as a gap, never as a dry hour.** It is shaded
    grey and the cumulative line stops at the first one: a running total that
    stepped over it would count the hour as 0 mm, which is the bug the gap
    audit exists to prevent (see `reports._gap_section`).

    Drawn with the object API rather than pyplot: the console server runs
    workflows on threads, and pyplot's global figure state is not safe there.
    """
    import textwrap
    from datetime import datetime, timedelta

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import AutoDateLocator, DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch

    meta = payload.get("meta") or {}
    src = meta.get("source") or {}
    times = (payload.get("hourly") or {}).get("time") or []
    rain = ((payload.get("hourly") or {}).get("vars") or {}).get(
        "precipitation", {}).get("mean") or []
    if not times or not rain:
        raise ValueError("the payload has no hourly AOI-mean precipitation to draw")

    def at(stamp: str) -> datetime:
        return datetime.strptime(stamp.rstrip("Z")[:16], "%Y-%m-%dT%H:%M")

    hours = [at(t) for t in times[:len(rain)]]
    cumulative: list[float | None] = []
    total, broken = 0.0, False
    for value in rain:
        if value is None:
            broken = True
        if broken:
            cumulative.append(None)
        else:
            total += value
            cumulative.append(total)

    days = max(1.0, (hours[-1] - hours[0]).total_seconds() / 86400 + 1 / 24)
    # The caption carries the source and dated model hours (BUG-020) and
    # runs to several lines, so the figure grows to hold it: the plot keeps
    # its size, and the legend and caption are placed in inches from the
    # bottom instead of in fractions that collide as the caption grows.
    width_in = min(13.0, max(7.5, 3.8 + days * 0.55))
    grid = payload.get("grid") or {}
    zone = dating.zone_at(dating.bbox_of({}, payload))
    caption = (f"Hourly rainfall averaged over {grid.get('n_points', '?')} grid points "
               f"at {grid.get('spacing_deg', '?')} deg, equally weighted, "
               f"{src.get('native_res_km', '?')} km model. "
               + dating.figure_line_for("weather_grid", payload, zone))
    # Wrapped by hand: fig.text does not wrap inside the figure on its own.
    caption = textwrap.fill(caption, int(width_in * 0.84 * 72 / (6.5 * 0.55)))
    caption_in = 0.07 + 0.115 * (caption.count("\n") + 1)
    legend_in = caption_in + 0.06
    bottom_in = legend_in + 0.90
    height_in = 3.36 + bottom_in
    fig = Figure(figsize=(width_in, height_in), dpi=170)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.08, right=0.90, top=1 - 0.736 / height_in,
                        bottom=bottom_in / height_in)

    width = timedelta(hours=1)
    # Bar width is in days on a date axis: one hour is 1/24.
    ax.bar([h for h, v in zip(hours, rain) if v is not None],
           [v for v in rain if v is not None], width=1 / 24, align="edge",
           color=HYETO_BAR, linewidth=0, zorder=3)

    top = max([v for v in rain if v is not None] or [1.0])
    for h, v in zip(hours, rain):
        if v is None:
            ax.axvspan(h, h + width, color=HYETO_GAP, alpha=0.8, hatch="///",
                       linewidth=0, zorder=1)

    summary = payload.get("event_summary") or {}
    for n, burst in enumerate(summary.get("rain_bursts") or [], start=1):
        start, end = at(burst["start_utc"]), at(burst["end_utc"]) + width
        ax.axvspan(start, end, color=HYETO_BURST, alpha=0.55, linewidth=0, zorder=0)
        floor = " (floor)" if burst.get("depth_is_lower_bound") else ""
        ax.text(start + (end - start) / 2, top * 1.24,
                f"B{n}: {burst.get('depth_mm', 0):.0f} mm{floor}",
                ha="center", va="bottom", fontsize=7, color="#2c5282")

    peaks = summary.get("max_intensity_mm") or {}
    if peaks.get("t_max_24h_utc") and peaks.get("24h") is not None:
        start = at(peaks["t_max_24h_utc"])
        ax.annotate("", xy=(start, top * 1.07), xytext=(start + timedelta(hours=24), top * 1.07),
                    arrowprops={"arrowstyle": "<->", "color": "#744210", "lw": 0.9})
        ax.text(start + timedelta(hours=12), top * 1.10, f"wettest 24 h: {peaks['24h']:.0f} mm",
                ha="center", va="bottom", fontsize=7, color="#744210")

    ax.set_ylim(0, top * 1.36)
    ax.set_ylabel("Rainfall, AOI mean (mm/h)", fontsize=8.5, color=HYETO_BAR)
    ax.tick_params(axis="y", labelsize=7.5, colors=HYETO_BAR)
    ax.set_xlim(hours[0], hours[-1] + width)
    locator = AutoDateLocator(minticks=4, maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(DateFormatter("%d %b\n%H:%M"))
    ax.tick_params(axis="x", labelsize=7.5)
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.6, zorder=0)
    for side in ("top",):
        ax.spines[side].set_visible(False)

    twin = ax.twinx()
    twin.plot(hours, [c if c is not None else float("nan") for c in cumulative],
              color=HYETO_CUM, linewidth=1.6, zorder=4)
    reached = max([c for c in cumulative if c is not None] or [0.0])
    twin.set_ylim(0, max(1.0, reached) * 1.15)
    twin.set_ylabel("Cumulative since window start (mm)", fontsize=8.5, color=HYETO_CUM)
    twin.tick_params(axis="y", labelsize=7.5, colors=HYETO_CUM)
    twin.spines["top"].set_visible(False)

    window = meta.get("window_utc") or [times[0], times[-1]]
    totals = summary.get("storm_total_mm") or {}
    storm = (f"storm total {totals['aoi_mean']:.1f} mm (AOI mean)"
             if totals.get("aoi_mean") is not None else
             "storm total withheld (gaps)")
    fig.suptitle(f"Rainfall hyetograph -- {window[0][:10]} to {window[1][:10]} (UTC)",
                 fontsize=11, x=0.08, ha="left", y=0.97)
    fig.text(0.08, 0.885, f"{storm}; {len(rain)} h; event {meta.get('event_id', '?')}",
             fontsize=8, color="#4a5568")

    legend = [Patch(facecolor=HYETO_BAR, label="hourly rainfall, AOI mean"),
              Patch(facecolor=HYETO_CUM, label="cumulative rainfall"),
              Patch(facecolor=HYETO_BURST, label="rain burst (B = depth)")]
    if any(v is None for v in rain):
        legend.append(Patch(facecolor=HYETO_GAP, hatch="///",
                            label="missing hour -- not counted, line stops"))
    fig.legend(handles=legend, loc="lower left", ncol=4, fontsize=7, frameon=False,
               bbox_to_anchor=(0.07, legend_in / height_in))
    fig.text(0.08, 0.07 / height_in, caption, fontsize=6.5,
             color="#4a5568", va="bottom")

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white")
    return {"name": "hyetograph", "png": str(out_png), "caption": caption,
            "hours": len(rain), "gap_hours": sum(1 for v in rain if v is None)}


# Module 6 as a picture (user, 2026-11-22): the white score table read as
# numbers only -- nothing said which rows are costs and which are benefits,
# or why a weight came out as it did. Every value here is read from the
# payload; the colour is each cell's place between the row's best and worst.
_BETTER, _MID, _WORSE = "#2f6db5", "#f0efec", "#c8473f"     # diverging pair, grey midpoint
_INK, _INK2, _MUTED = "#0b0b0b", "#52514e", "#898781"
_WORDS = {
    "P3": {0: "no", 1: "yes"},
    "P4": {0: "none", 1: "classes", 2: "per pixel"},
    "P6": {0: "manual", 1: "partial", 2: "full"},
}
_UNITS = {"P1": (" m", " m"), "P2": (" day", " days"), "P5": (" yr", " yrs")}
NEAR_ZERO = 0.001        # a weight below this cannot move the ranking; drawn faded
# Cloud and wind are read at the passes, local time (2026-11-22).
_INDEX_WORDS = {"cloud": ("cloud at optical passes", "cloud_pct", "%"),
                "wind": ("wind at radar passes", "wind_ms", " m/s"),
                "veg": ("dense vegetation", "veg_pct", "%"),
                "steep": ("steep land", "steep_pct", "%"),
                "urban": ("built-up", "urban_pct", "%")}
MATRIX_FOOTNOTE = (
    "How the weights arrive: the event criteria come first -- each starts at 1/12 and is "
    "raised by this event (52% dense vegetation raises L1 by 52%; cloud by its curve, wind by "
    "its ramp, steep and built-up land by their shares; daylight W3 is not raised). Cloud is "
    "the mean at the optical products' passes and wind at the radar passes, all in local "
    "time. The weight "
    "left is shared equally by the six product criteria P1-P6. "
    "Next, Module 7 (TOPSIS) multiplies each row by its weight and ranks the products by "
    "how close each comes to an imaginary product that is best on every row. "
    "Scores: product documents and sensor physics; sources listed under the answer.")


def _cell_word(cid: str, value: float) -> str:
    if cid in _WORDS:
        return _WORDS[cid].get(int(value), f"{value:g}")
    if cid in _UNITS:
        return f"{value:g}{_UNITS[cid][0 if value == 1 else 1]}"
    if cid.startswith(("W", "L")):          # susceptibility: how badly it is hurt
        return {0: "not hurt", 1: "hurt", 2: "badly hurt"}.get(int(value), f"{value:g}")
    return f"{value:g}"


def _mix(c1: str, c2: str, t: float) -> tuple[float, float, float]:
    a = [int(c1[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _goodness_colour(g: float | None) -> tuple[float, float, float]:
    if g is None:
        return _mix(_MID, _MID, 0)
    if g >= 0.5:
        return _mix(_MID, _BETTER, (g - 0.5) * 2)
    return _mix(_MID, _WORSE, (0.5 - g) * 2)


def decision_matrix_figure(payload: dict[str, Any], out_png: str | Path) -> dict[str, Any]:
    """Module 6 at a glance: benefit and cost criteria in two bands, each
    product's cell coloured by how it compares with the others on that row,
    and each criterion's weight as a bar beside the equal share it started
    from, with what this event did to it."""
    import textwrap

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    from .figures import finish_figure, styled_figure

    crit = [c for c in payload.get("criteria") or [] if isinstance(c, dict)]
    alts = [a for a in payload.get("alternatives") or [] if isinstance(a, dict)]
    values = (payload.get("decision_matrix") or {}).get("values") or {}
    if not crit or not alts or not values:
        raise ValueError("no decision matrix to draw")
    indices = payload.get("indices") or {}
    changed = {(c["criterion"], c["alternative"]) for c in payload.get("scores_changed") or []}
    # Only what the user named is theirs; the rest moved by the rescaling.
    w_changed = {c["id"]: c["stated"] for c in payload.get("weights_changed") or []
                 if c.get("stated") is not None}
    benefit = [c for c in crit if c.get("direction") == 1]
    cost = [c for c in crit if c.get("direction") != 1]
    equal = 1.0 / len(crit)

    label_w, cell_w, gap, bar_w = 6.2, 2.05, 0.5, 3.4
    x_cells = label_w
    x_bar = label_w + cell_w * len(alts) + gap
    x_why = x_bar + bar_w + 0.75
    total_w = x_why + 3.6
    n_rows = len(benefit) + len(cost)
    height_units = 1.6 + 2 * 0.9 + n_rows + 0.3 + 2.2
    fig, ax = styled_figure(plt, figsize=(14.0, 0.42 * height_units + 0.6), dpi=150)
    ax.set_xlim(0, total_w)
    top = -1.9
    ax.set_ylim(height_units + top, top)
    ax.axis("off")

    ax.text(0, -1.75, "Module 6 - how each flood product scores, and how much each "
            "criterion counts", fontsize=13, fontweight="bold", color=_INK, va="top")
    # Legend: what a colour means, and a changed cell.
    lx = 0.0
    for g, word in ((1.0, "better than the others on this row"),
                    (0.0, "worse than the others on this row"), (None, "all the same")):
        ax.add_patch(Rectangle((lx, -0.88), 0.34, 0.34, facecolor=_goodness_colour(g),
                               edgecolor="#d0d4da", linewidth=0.6))
        ax.text(lx + 0.45, -0.71, word, fontsize=8.5, color=_INK2, va="center")
        lx += 0.95 + 0.15 * len(word)
    if changed or w_changed:
        ax.add_patch(Rectangle((lx, -0.88), 0.34, 0.34, facecolor="white",
                               edgecolor=_INK, linewidth=1.6))
        ax.text(lx + 0.45, -0.71, "changed by you", fontsize=8.5, color=_INK2, va="center")

    # Column heads.
    for j, a in enumerate(alts):
        cx = x_cells + cell_w * (j + 0.5)
        name = a.get("name", a["id"])
        for prefix in ("OPERA ", "MODIS ", "VIIRS "):
            name = name.replace(prefix, prefix.strip() + "\n")
        ax.text(cx, 0.05, name.replace(" (", "\n("), ha="center",
                va="center", fontsize=8.5, fontweight="bold", color=_INK)
        sar = str(a.get("sensor", "")).lower() == "sar"
        ax.text(cx, 0.85, "radar, sees through\ncloud" if sar else "optical, needs\nclear sky",
                ha="center", va="center", fontsize=6.8, color=_MUTED)
    ax.text(x_bar, 0.05, "Weight in the ranking", fontsize=8.5, fontweight="bold",
            color=_INK, va="center")
    ax.text(x_bar, 0.72, f"dashed line: equal start, 1/{len(crit)} = {equal:.3f}",
            fontsize=6.8, color=_MUTED, va="center")
    ax.text(x_why, 0.05, "What this event did", fontsize=8.5, fontweight="bold",
            color=_INK, va="center")
    w_max = max([float(c.get("weight") or 0) for c in crit] + [equal]) * 1.12

    y = 1.55
    for title, colour, group in (("BENEFIT: higher is better", _BETTER, benefit),
                                 ("COST: lower is better", _WORSE, cost)):
        ax.add_patch(FancyBboxPatch((0.02, y - 0.34), total_w - 0.04, 0.62,
                                    boxstyle="round,pad=0.0,rounding_size=0.12",
                                    facecolor=_mix(_MID, colour, 0.16), edgecolor="none"))
        arrow = "▲ " if group is benefit else "▼ "
        ax.text(0.15, y - 0.03, arrow + title, fontsize=9.5, fontweight="bold",
                color=colour, va="center")
        y += 0.9
        for c in group:
            cid = c["id"]
            row = [float(values[a["id"]][cid]) for a in alts]
            lo, hi = min(row), max(row)
            weight = float(c.get("weight") or 0.0)
            faded = weight < NEAR_ZERO
            ax.text(0.15, y, f"{cid}  {c.get('name', '')}", fontsize=8.8, va="center",
                    color=_MUTED if faded else _INK)
            for j, (a, v) in enumerate(zip(alts, row)):
                if hi == lo:
                    g = None
                elif c.get("direction") == 1:
                    g = (v - lo) / (hi - lo)
                else:
                    g = (hi - v) / (hi - lo)
                mine = (cid, a["id"]) in changed
                ax.add_patch(Rectangle((x_cells + cell_w * j + 0.04, y - 0.42), cell_w - 0.08,
                                       0.84, facecolor=_goodness_colour(g),
                                       edgecolor=_INK if mine else "white",
                                       linewidth=1.8 if mine else 1.2,
                                       alpha=0.35 if faded else 1.0))
                strong = g is not None and abs(g - 0.5) > 0.36 and not faded
                ax.text(x_cells + cell_w * (j + 0.5), y, _cell_word(cid, v), ha="center",
                        va="center", fontsize=8.2, color="white" if strong else _INK,
                        alpha=0.55 if faded else 1.0)
            # The weight: a bar, the equal start behind it, and why it moved.
            length = max(bar_w * weight / w_max, 0.03)
            ax.add_patch(Rectangle((x_bar, y - 0.22), length, 0.44,
                                   facecolor=_MUTED if faded else _INK2,
                                   edgecolor=_INK if cid in w_changed else "none",
                                   linewidth=1.8))
            ex = x_bar + bar_w * equal / w_max
            ax.plot([ex, ex], [y - 0.38, y + 0.38], color=_MUTED, linewidth=0.9,
                    linestyle=(0, (2, 2)))
            ax.text(x_bar + length + 0.1, y, f"{weight:.3f}", fontsize=8.5, va="center",
                    fontweight="bold", color=_MUTED if faded else _INK,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.6})
            why = ""
            by = c.get("modulated_by")
            if cid in w_changed:
                why = f"set by you to {w_changed[cid]:g}, then rescaled"
            elif by in _INDEX_WORDS:
                word, key, unit = _INDEX_WORDS[by]
                v = indices.get(key)
                mod = float(c.get("modulator") or 0.0)
                vtxt = f"{v:.3g}{unit}" if isinstance(v, (int, float)) else "?"
                inc = float(c.get("increase") if c.get("increase") is not None else mod)
                why = (f"{word} {vtxt}: no effect" if weight < NEAR_ZERO
                       else f"{word} {vtxt}: +{inc * 100:.0f}%")
            elif cid.startswith("P"):
                why = "share of what is left"
            else:
                why = "not raised"
            ax.text(x_why, y, why, fontsize=7.8, va="center",
                    color=_MUTED if why in ("not raised", "share of what is left") else _INK2)
            y += 1.0
        y += 0.15

    ax.text(0, y + 0.15, "\n".join(textwrap.wrap(MATRIX_FOOTNOTE, 175)), fontsize=7.8,
            color=_INK2, va="top")
    finish_figure(plt, fig, Path(out_png))
    return {"png": str(out_png), "criteria": len(crit), "products": len(alts)}


def closeness_meter(ranking: list[dict[str, Any]], out_png: str | Path) -> dict[str, Any]:
    """TOPSIS closeness as a meter (user, 2026-11-19: "make the closeness to
    ideal and anti-ideal more intuitive with a pictorial legend ... like an
    arrow or meter, like we use in sensitivity analysis").

    One horizontal scale from the anti-ideal (0) to the ideal (1), shaded from
    red to green, and one arrow per product pointing at its closeness, labelled
    with its rank and value. Drawn from the ranking rows as computed -- no
    number is recomputed here.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from .figures import finish_figure, styled_figure

    rows = sorted(ranking, key=lambda r: (r["rank"], r["alternative"]))
    if not rows:
        raise ValueError("no ranking rows to draw")
    n = len(rows)
    fig, ax = styled_figure(plt, figsize=(9.0, 2.2 + 0.55 * n), dpi=150)
    gradient = np.linspace(0, 1, 256).reshape(1, -1)
    ax.imshow(gradient, extent=[0, 1, -0.35, 0.0], aspect="auto", cmap="RdYlGn",
              alpha=0.9, zorder=1)
    ax.plot([0, 1], [0, 0], color="0.25", linewidth=0.8, zorder=2)
    for x in (0, 0.25, 0.5, 0.75, 1.0):
        ax.plot([x, x], [-0.35, -0.42], color="0.25", linewidth=0.8)
        ax.text(x, -0.48, f"{x:g}", ha="center", va="top", fontsize=9)
    ax.text(0, -0.78, "Anti-ideal\nworst value of every criterion", ha="left",
            va="top", fontsize=8.5, color="#a50026", fontweight="bold")
    ax.text(1, -0.78, "Ideal\nbest value of every criterion", ha="right",
            va="top", fontsize=8.5, color="#006837", fontweight="bold")

    for i, row in enumerate(rows):
        y = 0.45 + i * 0.55
        c = float(row["closeness"])
        ax.annotate("", xy=(c, 0.02), xytext=(c, y - 0.08),
                    arrowprops={"arrowstyle": "-|>", "color": "0.15", "lw": 1.2},
                    zorder=3)
        # Rows run best-first from the bottom, so every arrow drawn above a
        # label sits at a lower closeness: a label that starts at its own
        # arrow and runs right never crosses another arrow.
        ax.text(min(c, 0.70) + 0.006, y, f"{row['rank']}. {row['alternative']}  ({c:.3f})",
                ha="left", va="center", fontsize=9,
                bbox={"facecolor": "white", "edgecolor": "0.6", "pad": 2.5,
                      "boxstyle": "round,pad=0.25"}, zorder=4)
    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-1.25, 0.45 + n * 0.55)
    ax.axis("off")
    ax.set_title("TOPSIS closeness to the ideal", pad=8)
    fig.text(0.5, 0.01,
             "Closeness = distance from the anti-ideal / (distance from the anti-ideal + "
             "distance to the ideal). Higher is better; 1 would be best on every criterion. "
             "Source: rank_flood_products (Hwang and Yoon 1981).",
             ha="center", va="bottom", fontsize=8, color="0.3", wrap=True)
    finish_figure(plt, fig, Path(out_png))
    return {"png": str(out_png), "products": n}


def update_index(index_path: str | Path, row: dict[str, Any]) -> Path:
    """Add or replace one figure's row in an event's `figures/index.json`.

    Replaced by name rather than appended blindly: regenerating a figure
    should leave one row describing the file that is actually on disk, not two
    rows disagreeing about it.
    """
    index_path = Path(index_path)
    existing: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except json.JSONDecodeError:
            # A corrupt index is rebuilt around this row rather than crashing:
            # the PNGs are the artefacts, the index is a manifest of them.
            existing = []

    existing = [r for r in existing if r.get("name") != row["name"]]
    existing.append(row)
    # A module can list a figure drawn elsewhere (a tool's own chart) before
    # the store has a figures folder of its own.
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(existing, indent=1, default=str),
                          encoding="utf-8")
    return index_path
