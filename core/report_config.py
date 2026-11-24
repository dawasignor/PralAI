"""Per-experiment report settings that are decisions, not data.

Kept in code, beside the renderers that read them, so a setting is reviewed
like any other change and never lives only in a gitignored experiment folder.

**Climate zone**. The health output's risk list depends on
it -- standing water, mosquitoes and waterborne disease in the tropics; mould
in temperate or cold climates. A climate-classification dataset would be new
data and needs the user's approval, so the zone is a per-experiment value,
stated as such wherever it is used. An experiment with none is NOT KNOWN.

**The HEV knowledge notes** (D-35). The user writes five vault notes --
critical infrastructure, roads and accessibility, food, water, health -- and
Outputs 9 and 10 read them by exact title, each with its "<title> - sources"
companion (the flow convention: a note without one is refused). The titles
below are WORKING titles until the user confirms them (Q-1); a note that is
not in the vault makes its section say "no sourced evidence yet". Evidence is
never written here.
"""

from __future__ import annotations

from typing import Any

CLIMATE_ZONES = ("tropical", "temperate", "cold", "arid")

EXPERIMENTS: dict[str, dict[str, Any]] = {
    # The MAIN thesis experiment since 2026-11-18 (user decision, replacing
    # Gampola the same day): the Kelani river basin, Cyclone Ditwah,
    # 26 Nov - 4 Dec 2025. Reference: Copernicus EMS EMSR851 AOI02
    # (core/references.py, experiments/kelani-basin-2025/validation/).
    "kelani-basin-2025": {
        "event_name": "Cyclone Ditwah floods, Kelani river basin",
        "climate_zone": "tropical",
        "climate_zone_basis": "per-experiment config value, "
                              "not read from a climate dataset",
        "country_iso3": "LKA",
        "bbox": [79.9450, 6.8800, 80.1550, 7.0000],
        "window": ["2025-11-26", "2025-12-04"],
        "flood_extent_config": "experiments/kelani-basin-2025/validation/config.yaml",
        "tool_params": {"fetch_weather": {"local_utc_offset_h": 5.5}},
    },
    # Main experiment for part of 2026-11-18 (user decision): Gampola on
    # the Mahaweli, Cyclone Ditwah, 26 Nov - 4 Dec 2025. The AOI is larger
    # than both references on purpose (land cover and terrain around the
    # flood). References: core/references.py and
    # experiments/gampola-2025/validation/reference/README.md.
    "gampola-2025": {
        "event_name": "Cyclone Ditwah floods, Gampola (Mahaweli Ganga)",
        "climate_zone": "tropical",
        "climate_zone_basis": "per-experiment config value, "
                              "not read from a climate dataset",
        "country_iso3": "LKA",
        "bbox": [80.5460, 7.1368, 80.6310, 7.2450],
        "window": ["2025-11-26", "2025-12-04"],
        "flood_extent_config": "experiments/gampola-2025/validation/config.yaml",
        "tool_params": {"fetch_weather": {"local_utc_offset_h": 5.5}},
    },
    "kelani-lower-2025": {
        "event_name": "Cyclone Ditwah floods, lower Kelani river",
        "climate_zone": "tropical",
        "climate_zone_basis": "per-experiment config value, "
                              "not read from a climate dataset",
        "country_iso3": "LKA",
        # What the pipelines (core/pipelines/) call the tools with. The AOI is
        # the locked thesis AOI (2026-10-21); the extent config is the same
        # validation config get_flood_extent was first run with. Per-tool
        # parameters are facts about the place (its clock), never thresholds:
        # a threshold stays at the tool's default.
        "bbox": [79.94, 6.905, 80.09, 6.978],
        "flood_extent_config": "experiments/kelani-lower-2025/validation/config.yaml",
        "tool_params": {"fetch_weather": {"local_utc_offset_h": 5.5}},
    },
}

# Section -> the vault note it reads (D-35). Working titles; see the docstring.
HEV_NOTES: dict[str, str] = {
    "critical_infrastructure": "Critical infrastructure",
    "roads_and_accessibility": "Roads and accessibility",
    "food": "Food",
    "water": "Water",
    "health": "Health",
}
HEV_NOTES_CONFIRMED = False

# The person depth bands of D-25: body-segment heights (Drillis & Contini
# 1966, as tabulated in Winter) applied to the average adult height
# (NCD-RisC 2016), rounded to 5 cm. EMPTY until both figures are read from
# their sources -- the 2016 NCD-RisC paper reports heights by country and
# gives no global mean in its text, and the segment table was not readable
# (SOURCES.md 8g). Until then every person band is NOT KNOWN. Each entry,
# once sourced: {"band": "...", "from_m": .., "to_m": .., "source": "..."}.
PERSON_DEPTH_BANDS: list[dict[str, Any]] = []


def settings(experiment: str) -> dict[str, Any]:
    return dict(EXPERIMENTS.get(experiment, {}))


def climate_zone(experiment: str) -> tuple[str | None, str]:
    s = settings(experiment)
    zone = s.get("climate_zone")
    if zone is not None and zone not in CLIMATE_ZONES:
        raise ValueError(f"climate zone {zone!r} for {experiment} is not one of {CLIMATE_ZONES}")
    return zone, s.get("climate_zone_basis", "not set for this experiment")
