"""Tables as data: one module that renders a table for any module.

A table is kept as DATA -- columns, rows, a caption, its source -- and rendered
three ways from that one record, so the three can never disagree:

* markdown, for the module documents (core/outputs.py);
* JSON, served at `GET /api/table/<id>`, which the console draws as a table on
  a white card (`<table - t-...>` placeholders, like `<image - ...>` figures);
* a PNG on a white backdrop, for reports and PDF export.

Builders below turn a stored payload into its tables -- the MCDM decision
matrix and ranking, the sensitivity thresholds, the availability grid, class
shares, road km by class, and the uncertainty register Entro answers from.
**A builder copies values from the payload; it never computes a new number**
(a chart adds no analysis; the same goes for a table).

**Ids, not names.** A table id is `t-` and 12 hex characters of a hash of where
the table lives, recorded in one index (`outputs/tables_index.json`). That works
for any experiment, session or run -- figure placeholders need a `region-year`
experiment name, and `kelani-lower-2025` has none -- and the endpoint resolves
only an id the index holds, to a file under a folder tables may live in.
"""

from __future__ import annotations

import hashlib
import json
import re
import textwrap
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .experiment import ROOT

OUTPUTS = ROOT.parent / "outputs"
INDEX = OUTPUTS / "tables_index.json"
PLACEHOLDER = re.compile(r"<table\s*-\s*(t-[0-9a-f]{12})\s*>")
ID = re.compile(r"^t-[0-9a-f]{12}$")
PNG_MAX_ROWS = 40
_LOCK = threading.Lock()          # pyplot is not thread-safe; the server is


@dataclass
class Table:
    key: str
    title: str
    columns: list[str]
    rows: list[list[Any]]
    caption: str = ""
    source: str = ""
    notes: list[str] = field(default_factory=list)
    # Which columns are numbers, for right alignment; inferred when empty.
    numeric: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.numeric:
            self.numeric = [all(isinstance(r[i], (int, float)) and not isinstance(r[i], bool)
                                for r in self.rows if i < len(r) and r[i] not in (None, ""))
                            and any(i < len(r) and r[i] not in (None, "") for r in self.rows)
                            for i in range(len(self.columns))]

    # -- renders ---------------------------------------------------------------
    @staticmethod
    def cell(v: Any) -> str:
        if v is None:
            return "--"
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.1f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v).replace("|", "/").replace("\n", " ")

    def to_markdown(self, with_title: bool = False) -> str:
        out = [f"**{self.title}**", ""] if with_title else []
        out.append("| " + " | ".join(self.columns) + " |")
        out.append("|" + "".join("---:|" if n else "---|" for n in self.numeric))
        out += ["| " + " | ".join(self.cell(c) for c in r) + " |" for r in self.rows]
        if self.caption:
            out += ["", self.caption]
        return "\n".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rows": [[self.cell(c) if not isinstance(c, (int, float)) or
                                          isinstance(c, bool) else c for c in r] for r in self.rows]}

    def to_png(self, path: Path) -> Path:
        """The table on a white backdrop: a title, a bold header, banded rows.

        Column widths follow their content (a long text column gets the room),
        and the figure is exactly as tall as the wrapped rows need.
        """
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        rows = self.rows[:PNG_MAX_ROWS]
        text = [[self.cell(c) for c in r] for r in rows]
        # Characters per column: the longest cell, capped, then shares of ~140.
        need = [min(60, max([len(self.columns[i])] + [len(r[i]) for r in text if i < len(r)]))
                for i in range(len(self.columns))]
        total = max(1, sum(need))
        chars = [max(8, int(140 * n / total)) for n in need]
        nl = chr(10)
        wrap = lambda v, w: nl.join(textwrap.wrap(v, w)) or v  # noqa: E731
        cells = [[wrap(r[i] if i < len(r) else "", chars[i]) for i in range(len(chars))] for r in text]
        head = [wrap(c, chars[i]) for i, c in enumerate(self.columns)]
        lines = [max(1, *[c.count(nl) + 1 for c in r]) for r in cells] or [1]
        hlines = max(c.count(nl) + 1 for c in head)
        line_in = 0.19
        table_h = line_in * (sum(lines) + hlines) + 0.08 * (len(lines) + 1)
        foot = self.caption + (f"  ({len(self.rows) - PNG_MAX_ROWS} more rows in the data)"
                               if len(self.rows) > PNG_MAX_ROWS else "")
        top, bottom = 0.45, (0.35 if foot else 0.1)
        width = min(16.0, max(6.0, 0.085 * sum(chars)))
        height = top + table_h + bottom
        with _LOCK:
            fig = plt.figure(figsize=(width, height), dpi=150)
            fig.patch.set_facecolor("white")
            ax = fig.add_axes([0.01, bottom / height, 0.98, table_h / height])
            ax.axis("off")
            widths = [c / sum(chars) for c in chars]
            table = ax.table(cellText=cells or [[""] * len(head)], colLabels=head,
                             colWidths=widths, bbox=[0, 0, 1, 1], cellLoc="left", colLoc="left")
            table.auto_set_font_size(False)
            table.set_fontsize(8.5)
            units = [hlines] + lines
            unit_h = 1 / sum(units)
            for (r, c), cell in table.get_celld().items():
                cell.set_edgecolor("#d0d4da")
                cell.set_linewidth(0.6)
                cell.set_height(units[r] * unit_h)
                cell.PAD = 0.02
                if r == 0:
                    cell.set_facecolor("#eef1f5")
                    cell.set_text_props(weight="bold", color="#111111")
                else:
                    cell.set_facecolor("#ffffff" if r % 2 else "#f8f9fb")
                    if c < len(self.numeric) and self.numeric[c]:
                        cell.get_text().set_horizontalalignment("right")
                        cell._loc = "right"
            fig.text(0.01, 1 - 0.12 / height, self.title, fontsize=11, fontweight="bold",
                     va="top", color="#111111")
            if foot:
                fig.text(0.01, 0.06 / height, wrap(foot, 170), fontsize=8, color="#444444",
                         va="bottom")
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.12)
            plt.close(fig)
        return path


# -- storing and resolving -------------------------------------------------------------
def table_id(json_path: Path) -> str:
    return "t-" + hashlib.sha1(str(json_path.resolve()).lower().encode()).hexdigest()[:12]


def _allowed(path: Path) -> bool:
    # Read at call time, so a moved root (a test, a relocated checkout) is the
    # one checked.
    from . import cache_policy, experiment, session

    path = path.resolve()
    for root in (experiment.ROOT, session.SESSIONS_DIR, cache_policy.RUNS_DIR, OUTPUTS):
        try:
            path.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def save(table: Table, folder: Path, *, png: bool = True) -> dict[str, Any]:
    """Write `<folder>/tables/<key>.json` (and .png), and index it by id."""
    target = Path(folder) / "tables"
    target.mkdir(parents=True, exist_ok=True)
    jpath = target / f"{table.key}.json"
    jpath.write_text(json.dumps(table.to_dict(), indent=1, default=str), encoding="utf-8")
    ppath = table.to_png(target / f"{table.key}.png") if png else None
    tid = table_id(jpath)
    entry = {"id": tid, "placeholder": f"<table - {tid}>", "key": table.key,
             "title": table.title, "json": str(jpath), "png": str(ppath) if ppath else ""}
    with _LOCK:
        index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
        index[tid] = entry
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return entry


def resolve(tid: str, *, png: bool = False) -> Path:
    if not ID.match(tid or ""):
        raise KeyError(f"not a table id: {tid!r}")
    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    if tid not in index:
        raise KeyError(f"no table {tid}")
    path = Path(index[tid]["png" if png else "json"])
    if not path.is_file() or not _allowed(path):
        raise KeyError(f"table {tid} is not available")
    return path


def prompt_block(entries: list[dict[str, Any]] | None) -> str:
    if not entries:
        return ""
    return ("## Tables you may show\n"
            "To show one, put its placeholder on a line of its own in `answer`, exactly "
            "as written. The console draws it as a table; never retype its numbers.\n"
            + "\n".join(f"- `{e['placeholder']}` -- {e['title']}" for e in entries) + "\n\n")


# -- builders: payload -> tables ----------------------------------------------------------
def _ranking(p: dict) -> list[Table]:
    rows = [[r.get("rank"), r.get("alternative"), r.get("sensor"), r.get("closeness"),
             r.get("d_plus"), r.get("d_minus")]
            for r in sorted(p.get("ranking") or [], key=lambda r: r.get("rank", 99))]
    out = [Table("mcdm_ranking", "MCDM ranking (TOPSIS)",
                 ["Rank", "Product", "Sensor", "Closeness", "Distance to ideal", "Distance to anti-ideal"],
                 rows, caption="A rank is not a usability claim: read the cloud index beside it.",
                 source="rank_flood_products")]
    dm = (p.get("decision_matrix") or {}).get("values") or {}
    crit = [c for c in p.get("criteria") or [] if isinstance(c, dict)]
    alts = {a["id"]: a.get("name", a["id"]) for a in p.get("alternatives") or [] if isinstance(a, dict)}
    if dm and crit:
        cols = ["Criterion", "Weight", *[alts.get(a, a) for a in dm]]
        rows = [[f"{c['id']} {c.get('name', '')}", c.get("weight"),
                 *[dm[a].get(c["id"]) for a in dm]] for c in crit]
        out.append(Table("mcdm_decision_matrix", "MCDM decision matrix (scores and weights)",
                         cols, rows, caption="Scores are per product and criterion; the event "
                         "changes the weights, not the scores.", source="rank_flood_products"))
    return out


def _matrix_only(p: dict) -> list[Table]:
    """Module 6: the matrix and weights, no ranking (2026-11-19)."""
    return [t for t in _ranking({**p, "ranking": []}) if t.key != "mcdm_ranking"]


def _sensitivity(p: dict) -> list[Table]:
    rows = []
    for c in p.get("criteria") or []:
        up = (c.get("first_change") or {}).get("up") or {}
        down = (c.get("first_change") or {}).get("down") or {}
        rows.append([f"{c.get('criterion')} {c.get('name', '')}", c.get("weight"),
                     up.get("weight"), down.get("weight"), c.get("reachable")])
    return [Table("sensitivity_thresholds", "Sensitivity: the weight at which the order first changes",
                  ["Criterion", "Weight", "Change upward at", "Change downward at", "Reachable"],
                  rows, caption="Weights only are perturbed.", source="sensitivity_analysis")]


def _availability(p: dict) -> list[Table]:
    days = (p.get("period") or {}).get("days") or []
    code = {"none": 0, "not_checkable": "no data", "in_gibs": "G", "not_produced": "--",
            "unreachable": "?"}
    rows = [[prod.get("label"), *[(prod.get("days") or {}).get(d, {}).get("count", 0)
                                  if (prod.get("days") or {}).get(d, {}).get("status") == "available"
                                  else code.get((prod.get("days") or {}).get(d, {}).get("status"), "--")
                                  for d in days]]
            for prod in p.get("products") or []]
    return [Table("data_availability", "Passes over the AOI per day", ["Product", *[d[5:] for d in days]],
                  rows, caption="MODIS / VIIRS days LANCE no longer holds are counted from the "
                                "NASA GIBS daily composite (1 = it saw ground, 0 = only cloud); no data: "
                                "neither archive holds the day; ?: the search failed.",
                  source="data_availability")]


def _classes(key: str, title: str, fractions: dict, areas: dict | None, source: str) -> Table:
    rows = [[k.replace("_", " "), round(float(v) * 100, 2), (areas or {}).get(k)]
            for k, v in (fractions or {}).items() if v]
    return Table(key, title, ["Class", "Share %", "km2"], rows, source=source)


def _lulc(p: dict) -> list[Table]:
    f = ((p.get("indices") or {}).get("ndvi") or {}).get("classes") or {}
    return [_classes("ndvi_classes", "NDVI classes, share of observed land", f.get("fractions"),
                     f.get("area_km2"), "fetch_lulc_data")]


def _worldcover(p: dict) -> list[Table]:
    c = p.get("classes") or {}
    return [_classes("worldcover_classes", "WorldCover 2021 classes, share of land",
                     c.get("fractions"), c.get("area_km2"), "fetch_worldcover")]


def _terrain(p: dict) -> list[Table]:
    c = (p.get("slope") or {}).get("classes") or {}
    return [_classes("slope_classes", "Slope classes, share of land", c.get("fractions"),
                     c.get("area_km2"), "fetch_terrain_data")]


def _roads(p: dict) -> list[Table]:
    rows = [[k, v.get("flooded_km"), v.get("dry_km"), v.get("not_observed_km")]
            for k, v in sorted((p.get("roads_km_by_class") or {}).items(),
                               key=lambda kv: -kv[1].get("flooded_km", 0))]
    return [Table("roads_by_class", "Mapped road by class against the flood extent",
                  ["Road class", "Flooded km", "Dry km", "Not observed km"], rows,
                  caption=f"State: {p.get('road_state', '')}.", source="assess_road_access")]


BUILDERS: dict[str, Callable[[dict], list[Table]]] = {
    # data_availability offers NO table (user, 2026-11-19): Module 1 shows the
    # tool's own chart -- passes per day with the overpass cloud -- and the
    # per-day count grid duplicated it without the cloud. `_availability` is
    # kept for anything that asks for the grid by name.
    # mcdm_decision_matrix offers NO table either (user, 2026-11-22): Module 6
    # is its own figure (charts.decision_matrix_figure), with cost and benefit
    # criteria apart; `_matrix_only` is kept for anything that asks by name.
    "rank_flood_products": _ranking, "sensitivity_analysis": _sensitivity,
    "fetch_lulc_data": _lulc,
    "fetch_worldcover": _worldcover, "fetch_terrain_data": _terrain,
    "assess_road_access": _roads,
}


def uncertainty_table(entries: list[tuple[str, dict]], key: str = "uncertainty_register") -> Table:
    from .uncertainty import table_rows

    return Table(key, "Uncertainty by step", ["Result", "Step", "Level", "Uncertainty", "Basis"],
                 table_rows(entries), caption="Levels: input data, tool (processing), pipeline "
                 "(this run), decision. Read off the stored results; nothing is estimated.",
                 source="core/uncertainty.py")


def for_results(results: dict[str, Any]) -> list[Table]:
    """Every table a Monty batch's successful results yield, plus the batch's
    uncertainty register."""
    out: list[Table] = []
    done: list[tuple[str, dict]] = []
    for r in results.values():
        if r.get("status") != "success" or not isinstance(r.get("output"), dict):
            continue
        name = r.get("task") or ""
        try:
            built = BUILDERS.get(name, lambda p: [])(r["output"])
        except Exception:                                      # noqa: BLE001
            continue            # a payload a builder cannot read costs its table only
        if built:
            # The table's source and acquisition date, in the picture (BUG-020).
            from . import dating, workflows

            line = dating.figure_line_for(
                workflows.cache_name(name), r["output"],
                dating.zone_at(dating.bbox_of({}, r["output"])))
            for t in built:
                t.caption = f"{t.caption}  {line}".strip()
        out += built
        done.append((name, r["output"]))
    if done:
        out.append(uncertainty_table(done, "uncertainty_this_run"))
    return out
