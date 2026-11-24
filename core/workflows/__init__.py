"""Monty's tool registry.

Every task Monty can run is a first-party Python function registered here,
and Argos calls them by name -- the same shape as a batch of tool calls in one
request. Nothing is fetched and nothing is generated.

**These are "tools"**: a registered function returning
data processed deterministically, plus an uncertainty block, a sources block
and a run block (the tool contract, `apply_contract` below). They used to be
called workflows, and the folder, the session key `workflows_run` and
`@workflow` keep that name until the physical move (E-7). `@workflow` is an
alias of `@tool`, so no existing import breaks.

Registering one:

    @tool("fetch_weather", "Gridded event weather for a bounding box.")
    def fetch_weather(bbox, start_date, end_date):
        ...

Contract for a workflow function:

* Arguments are keyword arguments; Argos' `params` are bound to them directly,
  so parameter names are the public interface. Renaming one is a breaking
  change to Argos' prompt.
* Return a JSON-serialisable value -- a dict is the norm. Non-serialisable
  returns are reported as `unserialisable`, not silently coerced.
* Raise on failure. `core/monty_graph.py` catches per task, so one failing
  task never takes the batch down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Workflow:
    name: str
    description: str
    fn: Callable[..., Any]
    # Both for the console's navigator and flow graph, which used to hardcode
    # its own copy of this list -- so a workflow registered here stayed
    # invisible in the UI until somebody remembered to edit a TypeScript file.
    # `label` is a short human name; `source` is where the data actually comes
    # from, which is the first thing a reader asks of a number.
    label: str = ""
    source: str = ""
    # Display order, low first, ties broken alphabetically. A field rather than
    # a name special-cased in a sort, because the reason is a real property of
    # the workflow and not a UI preference: `fetch_place_context` produces the
    # bounding box every other workflow consumes, so it belongs at the top of a
    # list someone reads top-down. A hardcoded name in a comparator would rot
    # the first time one was renamed.
    order: int = 100
    # The tool's standing uncertainty (user decision 2026-10-30), short by
    # habit: what the DATA SOURCE cannot tell you, and what the METHOD adds. Fixed per tool, unlike the measured items a call returns;
    # "" means not written yet (`uncertainty_todo()` lists those).
    data_source_uncertainty: str = ""
    method_uncertainty: str = ""


REGISTRY: dict[str, Workflow] = {}

# Model uncertainty is the third kind, and it is not per tool: it is how much
# an answer moves when the same event is run again. It is assessed by running
# one event 10 times uncached (the RQ5 N = 10 batch, scripts/run_n.py) and
# comparing the runs (core/compare.py). Not done yet.
MODEL_UNCERTAINTY = {
    "status": "todo",
    "how": "run the same event 10 times uncached (RQ5 N = 10 batch, "
           "scripts/run_n.py) and compare the runs with core/compare.py",
}

def tool(name: str, description: str, label: str = "", source: str = "",
         order: int = 100, data_source_uncertainty: str = "",
         method_uncertainty: str = ""):
    """Decorator: register a function as a tool Monty can run."""

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in REGISTRY:
            raise ValueError(f"tool {name!r} is already registered")
        REGISTRY[name] = Workflow(name=name, description=description, fn=fn,
                                  label=label or name, source=source,
                                  order=order,
                                  data_source_uncertainty=data_source_uncertainty.strip(),
                                  method_uncertainty=method_uncertainty.strip())
        return fn

    return wrap


def tool_uncertainty(name: str) -> dict[str, Any]:
    """The standing uncertainty of one tool: data source, method, model."""
    entry = REGISTRY.get(canonical(name))
    return {"data_source": entry.data_source_uncertainty if entry else "",
            "method": entry.method_uncertainty if entry else "",
            "model": dict(MODEL_UNCERTAINTY)}


def uncertainty_todo() -> list[str]:
    """Registered tools whose data source or method statement is not written."""
    return sorted(n for n, e in REGISTRY.items()
                  if not (e.data_source_uncertainty and e.method_uncertainty))


# Kept until E-7 (ledger row B6): every module still registers with it.
workflow = tool


# ---------------------------------------------------------------------------
# Renamed and retired tools
# ---------------------------------------------------------------------------
# An old name a session record, a transcript or a model's habit may still
# carry, mapped to the tool that replaced it. Resolved with a `renamed-tool`
# log line; never registered twice, because the catalogue would then list two
# tools for one function.
RENAMED: dict[str, str] = {
    "fetch_weather_grid": "fetch_weather",          # D-6, 2026-10-25
}

# Out of the registry, file kept. `name -> (replacement, reason)`. A call by
# a retired name fails as `unknown_function` -- an existing error type, so
# Argos' debugger already knows it -- with the replacement in the message.
RETIRED: dict[str, tuple[str, str]] = {
    # User decision 2026-10-30: WorldPop is the one population source.
    "fetch_population": ("get_pop_data", "the census / HDX COD-PS / World Bank "
                         "chain was removed; population comes from WorldPop Global2"),
}

# Where a tool's result is cached, when the default (`fetch_` and `_data`
# stripped) would be wrong. `fetch_weather` keeps `weather_grid`: every
# experiment stores its weather as inputs/weather_grid.json, the fact sheet
# and the MCDM read that name, and plain `weather` was the retired point
# tool's.
CACHE_NAMES: dict[str, str] = {"fetch_weather": "weather_grid",
                               # inputs/population.json, read by the reports
                               "get_pop_data": "population"}


# Registered, code kept, but not available (user decision 2026-11-01):
# `name -> reason`. Left out of the routing catalogue, the next-step
# suggestions and the pipelines; a call by name fails as `unknown_function`
# with the reason. Remove a name here to bring the tool back.
INACTIVE: dict[str, str] = {
    "calc_flood_depth": "flood depth is not available in this version",
    "assess_road_access": "road access is not available in this version",
    "check_slope_tree_roads": "the slope-and-tree road check is not available "
                              "in this version",
}


def available(name: str) -> bool:
    """True for a registered tool that is not marked inactive."""
    name = canonical(name)
    return name in REGISTRY and name not in INACTIVE


def retired(name: str, replacement: str, reason: str):
    """Decorator: keep a function importable but not routable."""

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        RETIRED[name] = (replacement, reason)
        return fn

    return wrap


def canonical(name: str) -> str:
    """The current name for a tool name, following renames."""
    return RENAMED.get(name, name)


def cache_name(name: str, params: dict | None = None) -> str:
    """The input name a tool's result is cached under. A flood map is one per
    product (user decision 2026-11-12): `flood_map_<product>`, so asking for a
    second product never overwrites the first. Without params it is the tool's
    generic name, which is what attribution and comparison key on."""
    name = canonical(name)
    if name == "get_flood_map" and params and params.get("product"):
        return flood_map_name(str(params["product"]))
    return CACHE_NAMES.get(name) or name.removeprefix("fetch_").removesuffix("_data")


def flood_map_name(product: str) -> str:
    """`flood_map_<catalogue id>` for a product id or its ranking label."""
    from .flood_map import BY_LABEL

    return f"flood_map_{BY_LABEL.get(product, product)}"


def tool_of(stored_name: str) -> str:
    """The tool behind a stored input's name (the reverse of cache_name)."""
    if stored_name.startswith("flood_map_"):
        return "get_flood_map"
    return next((t for t in names(include_inactive=True) if cache_name(t) == stored_name),
                stored_name)


def resolve(name: str) -> tuple["Workflow | None", str]:
    """(tool, note) for a name as sent. `note` says what the lookup did:
    "" for a direct hit, "renamed" when an old name was followed, "inactive",
    "retired" or "unknown" when there is no tool to run."""
    if canonical(name) in INACTIVE:
        return None, "inactive"
    if name in REGISTRY:
        return REGISTRY[name], ""
    if name in RENAMED and RENAMED[name] in REGISTRY:
        return REGISTRY[RENAMED[name]], "renamed"
    if name in RETIRED:
        return None, "retired"
    return None, "unknown"


# ---------------------------------------------------------------------------
# The tool contract
# ---------------------------------------------------------------------------
# Four levels of uncertainty, RQ6. A tool fills `input` and `tool`; the
# pipeline runner fills `pipeline`; decision steps fill `decision`.
UNCERTAINTY_LEVELS = ("input", "tool", "pipeline", "decision")
CONTRACT_KEYS = ("uncertainty", "sources", "run")


def _contract_sources(value: Any) -> bool:
    """True for the contract's shape: a list of objects with at least an id
    or a title. Several tools already return `sources` as a list of strings
    or a table of their own; those are left exactly as they are."""
    return isinstance(value, list) and all(
        isinstance(v, dict) and ("id" in v or "title" in v) for v in value)


def apply_contract(output: Any, run: dict[str, Any]) -> tuple[Any, list[str]]:
    """Add the contract blocks a tool did not return. Returns (output, notes).

    **Back-compatible, never a failure.** A tool that returns no blocks gets
    empty ones, and each addition is named in `notes` so the caller can log
    `contract-missing` -- that is how the tools still to be converted are
    found. Nothing a tool returned is overwritten or removed: a legacy
    `sources` list keeps its shape and is noted as legacy.

    `run` is always written by the caller (run id, cache policy, timing,
    attempts, source failures), because only the caller knows them; a tool's
    own `run` key would be a contract breach and is kept under `run.tool_run`
    rather than lost.
    """
    notes: list[str] = []
    if not isinstance(output, dict):
        return output, [f"output is {type(output).__name__}; no blocks added"]
    out = dict(output)

    block = out.get("uncertainty")
    if not isinstance(block, dict):
        if block is not None:
            notes.append("uncertainty not an object; kept as uncertainty.legacy")
            block = {"legacy": block}
        else:
            notes.append("uncertainty")
            block = {}
    for level in UNCERTAINTY_LEVELS:
        if not isinstance(block.get(level), list):
            block[level] = [] if block.get(level) is None else [block[level]]
    out["uncertainty"] = block

    if "sources" not in out:
        notes.append("sources")
        out["sources"] = []
    elif not _contract_sources(out["sources"]):
        notes.append("sources legacy shape")

    run_block = dict(run)
    if "run" in out:
        notes.append("tool returned its own run key; kept as run.tool_run")
        run_block["tool_run"] = out["run"]
    out["run"] = run_block
    return out, notes


def listing() -> list[dict[str, str]]:
    """The registry as plain data, for anything outside Python to read.

    Served by `api/console_server.py` at `/api/workflows`. Signatures are
    included because the console shows them and because they are the only
    truth about parameter names -- the same reason the vault catalogue
    deliberately carries none.

    Ordered by `order` then name, so a reader meets `fetch_place_context`
    first: it produces the bounding box the rest consume, and a list that
    opens with the alphabet puts the starting point in the middle. `names()`
    stays alphabetical -- it feeds the routing prompt and the catalogue check,
    where a stable, boring order is the useful one.
    """
    import inspect

    from .. import one_liners

    out = []
    for name in sorted(REGISTRY, key=lambda n: (REGISTRY[n].order, n)):
        entry = REGISTRY[name]
        out.append({
            "id": entry.name,
            "label": entry.label,
            "source": entry.source,
            "description": entry.description,
            "uncertainty": tool_uncertainty(entry.name),
            "available": entry.name not in INACTIVE,
            **({"not_available": INACTIVE[entry.name]} if entry.name in INACTIVE else {}),
            # The plain line the console shows on click (core/one_liners.py),
            # and whether it is a data input "Check inputs" lists.
            "summary": one_liners.TOOLS.get(entry.name, ""),
            "dataInput": entry.name in one_liners.DATA_INPUTS,
            **({"inputOrder": one_liners.DATA_INPUTS.index(entry.name)}
               if entry.name in one_liners.DATA_INPUTS else {}),
            "params": [
                p.name for p in inspect.signature(entry.fn).parameters.values()
                if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
            ],
        })
    return out


def get(name: str) -> Workflow | None:
    return REGISTRY.get(name)


def names(include_inactive: bool = False) -> list[str]:
    """The tools that can be run; with `include_inactive`, every registered one."""
    return sorted(n for n in REGISTRY if include_inactive or n not in INACTIVE)


def catalogue() -> str:
    """One line per workflow -- goes into Argos' routing prompt."""
    import inspect

    lines = []
    for name in names():
        entry = REGISTRY[name]
        signature = ", ".join(
            p.name for p in inspect.signature(entry.fn).parameters.values()
            if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
        )
        lines.append(f"- {name}({signature}) -- {entry.description}")
    if INACTIVE:
        lines.append("\nNOT AVAILABLE -- registered but switched off; never call "
                     "or suggest these: " + ", ".join(sorted(INACTIVE)) + ".")
    return "\n".join(lines)


# Importing a module registers its workflows. Add new modules here.
from . import weather   # noqa: E402,F401  (import for side effect)
from . import sentinel  # noqa: E402,F401
from . import terrain   # noqa: E402,F401
from . import water     # noqa: E402,F401
from . import place     # noqa: E402,F401
from . import weather_grid  # noqa: E402,F401
from . import mcdm  # noqa: F401,E402
from . import sensitivity  # noqa: F401,E402
from . import availability  # noqa: F401,E402
from . import accuracy  # noqa: F401,E402
from . import osm_context  # noqa: F401,E402
from . import worldcover  # noqa: F401,E402
from . import context  # noqa: F401,E402
from . import population  # noqa: F401,E402
from . import disasters  # noqa: F401,E402
from . import impact  # noqa: F401,E402
from . import depth  # noqa: F401,E402
from . import flood_map  # noqa: F401,E402
from . import hazard_map  # noqa: F401,E402
