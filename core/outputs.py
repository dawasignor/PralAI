"""The store's inputs by role, and the helpers the module blocks and the
report facts share.

Every module, 1-12, is rendered from its live vault template by
`core/report_render.py`; the paragraphs and tables of Modules 1-10 are built
in `core/module_blocks.py`. This module holds what both read: the role ->
stored-name aliases, `Inputs`, the best-ranked product, the depth for a given
extent, and the small formatters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import report_config

NOT_KNOWN = "Not known yet --"

# A stored input can sit under more than one name: older experiments cached
# by hand (`availability`, `mcdm`, `sensitivity`) and Monty's cache_name rule.
ALIASES: dict[str, tuple[str, ...]] = {
    "availability": ("data_availability", "availability"),
    "weather": ("weather_grid",),
    "lulc": ("lulc",),
    "worldcover": ("worldcover",),
    "terrain": ("terrain",),
    "water": ("permanent_water",),
    "where": ("where_am_i",),
    "ranking": ("rank_flood_products", "mcdm"),
    # Module 6 alone (2026-11-19): the matrix and weights before any ranking.
    "matrix": ("mcdm_decision_matrix",),
    "sensitivity": ("sensitivity_analysis", "sensitivity"),
    "extent": ("get_flood_map", "get_flood_extent", "flood_extent"),
    "depth": ("calc_flood_depth",),
    "roads": ("assess_road_access",),
    "slope_tree": ("check_slope_tree_roads",),
    "population": ("population",),
    "wb": ("world_bank_indicators",),
    "inform": ("inform_risk",),
    "disasters": ("search_previous_disasters",),
    "hazard_map": ("map_hazard_exposure",),
}
TOOL_FOR = {"availability": "data_availability", "weather": "fetch_weather",
            "lulc": "fetch_lulc_data", "worldcover": "fetch_worldcover",
            "terrain": "fetch_terrain_data", "water": "fetch_permanent_water",
            "where": "where_am_i", "ranking": "rank_flood_products",
            "matrix": "mcdm_decision_matrix",
            "sensitivity": "sensitivity_analysis", "extent": "get_flood_map",
            "depth": "calc_flood_depth",
            "roads": "assess_road_access", "slope_tree": "check_slope_tree_roads",
            "population": "get_pop_data", "hazard_map": "map_hazard_exposure",
            "inform": "fetch_inform_risk", "disasters": "search_previous_disasters"}


class Inputs:
    """The store's cached inputs, looked up by role, with what was used."""

    def __init__(self, store: Any):
        self.store = store
        self.used: list[str] = []

    def get(self, role: str) -> dict[str, Any] | None:
        return self.get_named(role)[1]

    def get_named(self, role: str) -> tuple[str | None, dict[str, Any] | None]:
        """The payload and the name it is stored under (an older alias, maybe).

        "extent" is the MODULE PRODUCT's flood map -- the user's choice, else
        the best-ranked (module_product, 2026-11-18): one map is stored per
        product (`flood_map_<id>`, user decision 2026-11-12). `extent_for`
        names another one."""
        names = ALIASES[role]
        if role == "extent":
            top, _, _ = module_product(self.store, self)
            names = ((flood_map_store_name(top),) if top else ()) + names
        for name in names:
            record = self.store.cached_input(name)
            if record and isinstance(record.get("payload"), (dict, list)):
                if name not in self.used:
                    self.used.append(name)
                return name, record["payload"]
        return None, None

    def extent_for(self, product: str) -> dict[str, Any] | None:
        """One product's stored flood map (`flood_map_<id>`, or an older
        `get_flood_map` record of that product), or None."""
        from .workflows.flood_map import BY_LABEL

        key = BY_LABEL.get(product, product)
        for name in (flood_map_store_name(key),) + ALIASES["extent"]:
            record = self.store.cached_input(name)
            payload = (record or {}).get("payload")
            if isinstance(payload, dict) and payload.get("product") == key:
                if name not in self.used:
                    self.used.append(name)
                return payload
        return None


def flood_map_store_name(product: str) -> str:
    from .workflows import flood_map_name

    return flood_map_name(product)

# -- small formatting ------------------------------------------------------------
def _n(v: Any, places: int = 2, unit: str = "") -> str:
    if v is None:
        return "--"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        out = f"{v:,}"
    elif isinstance(v, float):
        out = f"{v:,.{places}f}"
    else:
        out = str(v)
    return f"{out} {unit}".rstrip()


def _pct(fraction: Any, places: int = 1) -> str:
    return "--" if fraction is None else f"{float(fraction) * 100:.{places}f}%"


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _window(store: Any) -> list[str]:
    """The experiment's window from event.md, as [start, end] or []."""
    path = getattr(store, "memory_path", None)
    if path is None or not Path(path).exists():
        return []
    for line in Path(path).read_text(encoding="utf-8").splitlines()[:12]:
        if line.startswith("window:"):
            parts = line.split(":", 1)[1].replace("to", " ").split()
            return parts[:2] if len(parts) >= 2 else []
    return []


# -- shared by the module blocks and the report facts -----------------------------
def module_product(store: Any, inp: Inputs) -> tuple[str | None, str, str]:
    """The product the flood map and Modules 9-10 describe: (id, label, how).

    The user's choice when they named one (core/product_choice.py, user
    decision 2026-11-18), otherwise the best-ranked product. `how` is
    "chosen" or "best-ranked"; with neither, (None, why, "").
    """
    from . import product_choice

    choice = product_choice.get(store)
    if choice:
        return choice["id"], product_choice.label(choice["id"], inp.get("ranking")), "chosen"
    top, label = best_ranked(store, inp)
    return (top, label, "best-ranked") if top else (None, label, "")


def best_ranked(store: Any, inp: Inputs) -> tuple[str | None, str]:
    p = inp.get("ranking")
    if p is None:
        return None, "the ranking is not stored"
    top = min(p.get("ranking") or [], key=lambda r: r.get("rank", 99), default=None)
    return (top["id"], top.get("alternative", top["id"])) if top else (None, "the ranking is empty")


def _note_status(section: str) -> tuple[bool, str]:
    """Is the section's vault note there, with its sources companion (Q-1)?"""
    from . import vault

    title = report_config.HEV_NOTES[section]
    try:
        index = vault.build_index()
        vault.read_note(index, title)
        vault.read_note(index, f"{title} - sources")
        return True, title
    except Exception as exc:                                   # noqa: BLE001
        return False, f"no sourced evidence yet -- the note \"{title}\" or its sources note " \
                      f"is not in the vault ({type(exc).__name__})"


def depth_for(inp: Inputs, extent: dict | None) -> tuple[dict | None, str]:
    """The stored depth, only when it was computed from THIS extent -- a depth
    from another product's extent describes another flood."""
    if not extent or extent.get("status") != "flood_mapped":
        return None, "there is no mapped flood extent to compute depth from"
    dep = inp.get("depth")
    if dep is None:
        return None, "the flood depth is not computed (run calc_flood_depth)"
    if dep.get("extent_raster") != (extent.get("raster") or {}).get("path"):
        return None, (f"the stored depth was computed from another extent "
                      f"({dep.get('product')}), not {extent.get('label')}")
    return dep, ""


def depth_band_rows(dep: dict) -> list[list[Any]]:
    """Depth bands as table rows: the vehicle classes (D-36), the 10 cm
    advisory (D-16) and the person bands (D-25) or why they are not known."""
    rows = [[c["class"], f"{c['from_m']:g}-{c['to_m']:g} m" if c["to_m"] is not None
             else f"over {c['from_m']:g} m", _n(c["km2"], 2), _pct(c["share"]),
             _pct(c["stable_share"]) if c["stable_share"] is not None else "--"]
            for c in dep.get("vehicle_classes") or []]
    adv = dep.get("advisory_10cm") or {}
    rows.append(["risk for small children, pushchairs and trolleys", f"from {adv.get('threshold_m', 0.1):g} m",
                 _n(adv.get("km2_at_or_above"), 2), _pct(adv.get("share_at_or_above")), "--"])
    person = dep.get("person_bands")
    if isinstance(person, dict) and person.get("status") == "not_known":
        rows.append(["depth for an adult (knee, hip, chest)", "--", "--", "--",
                     f"not known yet: {person.get('reason')}"])
    return rows


def _bridges_line(bridges: list[dict]) -> str:
    from .workflows.impact import group_bridges

    g = group_bridges(bridges)
    if not g["segments"]:
        return "No mapped bridge lies in the flood extent."
    names = ", ".join(g["named"][:8]) + (f" and {g['named_count'] - 8} more" if g["named_count"] > 8 else "")
    parts = [f"{g['named_count']} named" + (f" ({names})" if names else "")] if g["named_count"] else []
    if g["unnamed_sections"]:
        parts.append(f"{g['unnamed_sections']} unnamed bridge section(s)")
    return (f"Bridges in the flood extent, deck height not known: {' and '.join(parts)} "
            f"-- {g['segments']} mapped segments in all.")
