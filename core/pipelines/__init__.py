"""Named reports are pipelines, chosen by mode.

    flood_analyst   Modules 1-10
    flood_bot       Module 11, the Public Report     (after flood_analyst, D-24)
    chanakya        Module 12, the Executive Summary (after flood_analyst, D-24)

Free chat stays agent-routed (core/graph.py). A pipeline is a fixed list of
tool calls, renders and bounded agent-writing steps; all three read the same
stored results, so two modes cannot disagree on a number.

    python run.py --pipeline flood_analyst --experiment kelani-lower-2025 [--no-cache] [--run-id ID]
"""

from __future__ import annotations

from .base import AgentCall, Pipeline, PipelineRefused, model_writer

ORDER = ("flood_analyst", "flood_bot", "chanakya")


def get(name: str) -> Pipeline:
    from . import chanakya, flood_analyst, flood_bot

    builders = {"flood_analyst": flood_analyst.build, "flood_bot": flood_bot.build,
                "chanakya": chanakya.build}
    if name not in builders:
        raise KeyError(f"no pipeline {name!r}; known: {', '.join(ORDER)}")
    return builders[name]()


__all__ = ["AgentCall", "ORDER", "Pipeline", "PipelineRefused", "get", "model_writer"]
