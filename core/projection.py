"""Which fields of a cached payload a flow's consumer is actually entitled to.

`stats_only` answers "no rasters". This answers the narrower question: of the
statistics that remain, which ones does *this* consumer need?

**Every path below traces to a line in the consumer's own schema.** The
`norwood/pre-flood-aoi-brief` contract states its sources outright --

    aoi                            <- each payload's aoi block
    input_integrity.footprint      <- payload.aoi.bbox_requested vs
                                      bbox_covered, and grid.transform
    input_integrity.scene_selection<- payload.selection.attempts
    observation_basis              <- payload.indices.*.stats.coverage
                                      and payload.scl_counts
    composition_reliability        <- class fractions

-- plus the sections whose source is named in the field rather than a `from`
line: `recency` needs `scene`, `surface_composition` needs the index statistics
and the NDVI class fractions, `hydrography` needs the water occurrence classes,
`terrain_setting` needs elevation and slope.

The projection is written out rather than inferred from the schema text at run
time, because those `from` lines are prose ("each payload's aoi block") and a
parser for them would be guessing. What *is* automated is the check:
`tests/test_projection.py` fails if a schema `from` line names a payload key
this file does not serve, so the two cannot drift apart silently.

**Serving less than a consumer needs is the dangerous direction.** A missing
field does not raise -- the agent simply writes the brief without it and the
gap is invisible in the output. So when in doubt a path stays in, and the
narrowing is justified per key below rather than by eye.
"""

from __future__ import annotations

from typing import Any

# payload name -> the top-level keys that consumer may see, and why.
#
# A key absent from a list is withheld; a payload absent from the map is served
# whole, because withholding by default would make adding a workflow a silent
# starvation bug.
PROJECTIONS: dict[str, dict[str, list[str]]] = {
    "norwood/pre-flood-aoi-brief": {
        "lulc": [
            "aoi",          # aoi; input_integrity.footprint (bbox_requested vs covered)
            "grid",         # input_integrity.footprint (grid.transform)
            "scene",        # input_integrity.recency
            "indices",      # observation_basis, surface_composition, reliability
            "scl_counts",   # observation_basis.unobserved_cause, masking_policy
            "selection",    # input_integrity.scene_selection (attempts)
            "period",       # recency, event-window alignment
            "source",       # provenance for report_header.inputs
        ],
        "terrain": [
            "aoi",          # aoi.area_km2_by_source
            "grid",         # footprint
            "slope",        # terrain_setting.slope_deg + classes
            "elevation",    # terrain_setting.elevation_m
            "source",       # terrain_setting.model_type, dsm_caveat, provenance
        ],
        "permanent_water": [
            "aoi",          # aoi.area_km2_by_source
            "grid",         # footprint
            "thresholds",   # hydrography.occurrence_classes.threshold_pct
            "water",        # hydrography.occurrence_classes, look_alike_floor
            "occurrence_stats_over_water",   # hydrography, estimate spread
            "source",       # provenance
        ],
        # The event's weather, for event_commentary (the brief comments on
        # every figure with respect to the event; user 2026-11-02). The
        # hourly series is withheld: the summaries are what a comment needs.
        "weather_grid": ["meta", "event_summary", "derived", "qc", "figure"],
        "worldcover": ["aoi", "source", "classes", "shares", "figure"],
    },
}
# Norwood's comment on a result just fetched reads the same narrowed payloads.
PROJECTIONS["norwood/event-commentary"] = PROJECTIONS["norwood/pre-flood-aoi-brief"]


def project(payload: dict[str, Any], name: str, schema: str) -> dict[str, Any]:
    """`payload` narrowed to what `schema`'s consumer declares it needs.

    Unknown schema or unknown payload -> served whole. Withholding by default
    would turn "somebody added a workflow" into "the brief quietly lost a
    section", and that failure is invisible in the output.
    """
    allowed = (PROJECTIONS.get(schema) or {}).get(name)
    if not allowed:
        return payload
    return {key: value for key, value in payload.items() if key in allowed}


def withheld(payload: dict[str, Any], name: str, schema: str) -> list[str]:
    """Keys this projection drops. Reported, so the narrowing is visible."""
    allowed = (PROJECTIONS.get(schema) or {}).get(name)
    if not allowed:
        return []
    return sorted(key for key in payload if key not in allowed)
