"""One plain line on every tool and workflow (user decision 2026-11-02).

Shown when a node of the console's "Tools and workflows" graph is clicked,
beside each data input in "Check inputs", and written into the vault: the
Workflows MOC lists every one, and each module's template note opens with its
workflow's line. `tests/test_one_liners.py` checks that every available tool
and every thesis-path step has one, and that the vault carries them verbatim.

**Tools** are what Monty runs (the registry). **Workflows** are the thesis
steps an agent owns: Norwood every one but Modules 9 and 10, which are
Chanakya's.
"""

from __future__ import annotations

TOOLS: dict[str, str] = {
    "where_am_i": "Describes the area: its administrative units, towns, mapped facilities "
                  "and water infrastructure, from OpenStreetMap.",
    "data_availability": "Checks which of the five near-real-time flood products observed the "
                         "area on each day of the event.",
    "fetch_weather": "Fetches the event's hourly rain, cloud and wind over the area from a "
                     "named weather model.",
    "fetch_lulc_data": "Measures vegetation, built-up and water indices from the latest "
                       "cloud-free Sentinel-2 image before the event.",
    "fetch_worldcover": "Gives the share of each land-cover class from ESA WorldCover 2021, "
                        "the fallback when Sentinel-2 is too cloudy.",
    "fetch_terrain_data": "Measures elevation and slope over the area from the Copernicus "
                          "30 m elevation model.",
    "fetch_permanent_water": "Maps the water normally present, rivers, lakes and seasonal "
                             "water, from JRC Global Surface Water.",
    "mcdm_decision_matrix": "Shows each flood product's scores and how much each criterion "
                            "counts for this event, before any ranking.",
    "rank_flood_products": "Ranks the five flood products by how well each suits this event's "
                           "cloud, wind, vegetation, slope and built-up conditions.",
    "sensitivity_analysis": "Tests how far each criterion's weight can move before the "
                            "product ranking changes.",
    "get_flood_map": "Maps the flood from the best-ranked product over the area and window, "
                     "with how much of the land it observed.",
    "get_flood_extent": "Reads one product's flood extent from a validation set-up, for "
                        "checking it against a reference map.",
    "accuracy_metrics": "Measures how closely each flood product agrees with a reference "
                        "flood map.",
    "get_pop_data": "Counts the people in the area, and inside the mapped flood, from "
                    "WorldPop.",
    "fetch_inform_risk": "Gives the country's INFORM risk, vulnerability and coping-capacity "
                         "scores, out of 10.",
    "search_previous_disasters": "Lists the floods and storms recorded in the country before "
                                 "the event, from international registers.",
    "fetch_world_bank_indicators": "Gives the country's GDP and population from the World "
                                   "Bank.",
    "map_hazard_exposure": "Draws the flood with the hospitals, drinking-water facilities and "
                           "food outlets it reaches.",
}

# Thesis-path step id -> its line. The label (core/thesis_path.py) names it.
WORKFLOWS: dict[str, str] = {
    "where": "What and where the area is, before any analysis.",
    "availability": "Which flood products saw the area during the event.",
    "weather": "The rain, cloud and wind of the event.",
    "lulc": "What covered the ground before the flood.",
    "terrain": "How high and how steep the ground is.",
    "water": "The water normally there, so it is not mistaken for flood.",
    "brief": "The area before the flood, and how a flood map over it will behave.",
    "matrix": "Each flood product's scores, and how much each criterion counts here.",
    "ranking": "Which flood product suits this event best.",
    "sensitivity": "How firm that ranking is.",
    "essential": "The flood's reach to hospitals, drinking water and food, the people "
                 "affected, and what to do.",
    "health": "Sewage, drinking water, mosquitoes and mould after the flood, and what to do.",
    "accuracy": "How the flood products agree with a reference flood map.",
}

# Who owns a workflow in the graph: Chanakya Modules 9 and 10, Norwood the rest.
CHANAKYA_WORKFLOWS = ("essential", "health")


def owner(step_id: str) -> str:
    return "chanakya" if step_id in CHANAKYA_WORKFLOWS else "norwood"


# The data a session can draw on, in the order "Check inputs" lists them.
# Each turns green once fetched for the session's AOI and window. Derived
# results (ranking, sensitivity, the hazard map) are not data inputs.
DATA_INPUTS: tuple[str, ...] = (
    "where_am_i", "data_availability", "fetch_weather", "fetch_lulc_data",
    "fetch_worldcover", "fetch_terrain_data", "fetch_permanent_water", "get_flood_map",
    "get_pop_data", "fetch_inform_risk", "search_previous_disasters",
    "fetch_world_bank_indicators",
)
