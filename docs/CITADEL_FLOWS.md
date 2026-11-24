# CITADEL FLOWS

The catalogue of the workflows **Citadel** can run. Separate from
[WORKFLOWS.md](WORKFLOWS.md), which catalogues the data-fetching functions
**Monty** runs.

The two are different things and the distinction matters:

| | Monty's workflows | Citadel's flows |
|---|---|---|
| What it is | A registered Python function | An analysis defined by a prompt |
| Where it lives | `core/workflows/<topic>.py` | `agents/citadel/workflows/<name>.md` |
| How it is called | By name, with params, from Argos | Routed to by Argos, with `active_context` |
| What it returns | JSON | A written answer for the user |

A Citadel flow **consumes** Monty's workflows as inputs. It does not replace
them.

> **Adding a flow? Update this file as part of it.** An out-of-date catalogue
> is worse than none, because it is believed.

---

## The node contract

A **workflow** is a series of steps followed by one agent or several. A **node**
is one step in a workflow, and exists so the state can be followed.

Every Citadel flow is one Citadel node between two Argos nodes:

```
User -> Argos -> Citadel -> Argos -> User
        step 0   step 1     step 2
```

So for every flow in this file, `prev_node` is Argos carrying the user input,
`workflow_order` is 1, and `next_node` is Argos - which updates the session and
the logs and answers the user.

A node carries nine fields. The full definition, and the rules that hold across
every flow, are in the vault at
`Hand/Workflows/Chanakya flows/Chanakya node schema.md`.

| # | Field | Meaning |
|---|---|---|
| 1 | `prev_node` | Whose output starts this node. User input means step 0. |
| 2 | `title` | Technically relevant description, a few words |
| 3 | `parent_workflow` | For Citadel flows, the same as the title |
| 4 | `active_context` | User prompt, user persona, and the session so far, from Argos |
| 5 | `inputs_required` | Array of integers indexing the input sources below |
| 6 | `workflow_order` | The n-th step in the workflow |
| 7 | `task_description` | Every subtask, in detail |
| 8 | `expected_output` | A markdown sample of the response |
| 9 | `next_node` | Argos, or another agent |

---

## Where a flow lives: three parts, three consumers

A flow is **not** one document. It was, and that was wrong: the first written
flow came to 5,300 tokens -- three times Citadel's entire persona, for one of
eleven analyses, re-sent on every turn of the loop.

| Part | Holds | Lives in | How it reaches the model |
|---|---|---|---|
| **Card** | The procedure: when to select the flow, its inputs, the five-step spine, output and next-node rules | `## Flow Card` section of `Heart/Flow cards/Chanakya/<Title> - flow card.md` in the vault, named by `card_note` in `agents/citadel/workflows/<flow>.md` | Overlaid on the system prompt, one card per call |
| **Knowledge** | The evidence: figures, thresholds, case studies, cited short as `(WHO 2024)` | Vault, `Hand/Workflows/Chanakya flows/<Title>.md` | **MCP call 1**, at the top of the run |
| **Sources** | The full reference behind every citation key | Vault, `Hand/Workflows/Chanakya flows/<Title> - sources.md` | **MCP call 2**, at the top of the run |
| **Audit ledger** | Vintage and verification status of every source this project relies on | [SOURCES.md](SOURCES.md) | **Never** -- it is for a human checking the work |

**A figure appears in exactly one place.** The card carries no numbers and no
references, so it cannot drift from either note. The knowledge note carries a
figure and its short citation together. The sources note carries the full
reference for that citation, and is what gets shipped to the reader.

**Two retrievals, at the top of the run, not per step.** A flow opens by
fetching its knowledge note and its sources note. If either fails the flow
**refuses** rather than answering from memory -- a fabricated citation is worse
than a missing one, because it survives review.

**The server is built; the wiring is not.** `mcp_servers/vault_server.py`
serves three tools over stdio -- `read_note`, `search_notes`, `list_notes` --
over `core/vault.py`. A flow's two calls are two `read_note` calls, by the
titles in `knowledge_note` and `sources_note`.

```bash
VAULT_ROOT="<VAULT_ROOT>" \
    python -m mcp_servers.vault_server        # needs: pip install mcp
```

Each read returns the note verbatim plus its frontmatter, the vault commit it
was read at, an `uncommitted_changes` flag, and a **citation check**:
`citations.resolved` is false when a short citation has no reference behind it
in the sources note. The note is still served -- **fail open** -- so a caller in
strictly-verified-sources mode is the one that must refuse on that flag.

`core/tools.py::read_note` and `search_notes` are real, so Citadel can make
these calls from inside the graph.

### The card format

Frontmatter in `agents/citadel/workflows/<flow>.md`; the `## Flow Card` section in
the vault note named by its `card_note` (since 2026-10-27). Everything else the
loader needs is in the frontmatter:

```yaml
---
flow: human_health_risk
title: Human health risk
nexus: water
select_when: The user asks about sewage, wastewater, sanitation failure, ...
inputs_required: [1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 20]
knowledge_note: Human health risk
workflow_order: 1
prev_node: argos
next_node: argos
status: draft
---
```

`select_when` is the line Argos routes on. It is deliberately one sentence:
a routing menu is built from these, and eleven of them must fit in a prompt
that still has room for the session.

### Loading

```python
from core.spec import load_flow, list_flows

flow = load_flow("citadel", "human_health_risk")   # one FlowSpec
menu = list_flows("citadel")                            # all, for routing
```

`core.graph._flow_overlay` puts the chosen card into the specialist's system
prompt for that call only, via the same `extra_system` hook Argos uses to
become a debugger after a Monty failure. `GraphState` carries the choice in
`flow`.

A missing or malformed card is **logged and skipped**, not raised: the
specialist still answers from its own prompt, and the exchange log records that
the analysis was not applied.

**Argos selects the flow.** Every routing request carries a menu built from
the `select_when` lines, and Argos returns `flow` alongside `next`:

```json
{"next": "citadel", "flow": "human_health_risk", "brief": "..."}
```

`core.spec.valid_flow` checks the name belongs to that specialist. An invented
or borrowed name is **dropped and logged**, and the specialist answers on its
general path -- the same discipline as an unknown workflow name. Omitting
`flow` is the general path, and is the right answer more often than not.

The choice stays with Argos: `graph.py` builds the menu and checks the name,
and decides nothing. Per non-negotiable 5.

---

## Catalogue

Aimed at the **water-food-energy nexus**, with people's wellbeing at the
centre: how a flood reaches the people who depend on each resource.

| Flow | Nexus | Card | Evidence |
|---|---|---|---|
| Chanakya general question | - | [citadel_general_question.md](../agents/citadel/workflows/citadel_general_question.md) | none needed -- answers from the session |
| Critical infrastructure and waterlogging | water | [critical_infrastructure_and_waterlogging.md](../agents/citadel/workflows/critical_infrastructure_and_waterlogging.md) | not written |
| **Human health risk** | water | [human_health_risk.md](../agents/citadel/workflows/human_health_risk.md) | **knowledge + sources written** |
| Drinking water scarcity | water | [drinking_water_scarcity.md](../agents/citadel/workflows/drinking_water_scarcity.md) | not written |
| Primary cooking fuel disruption | food | [primary_cooking_fuel_disruption.md](../agents/citadel/workflows/primary_cooking_fuel_disruption.md) | not written |
| Essential services | food | [essential_services.md](../agents/citadel/workflows/essential_services.md) | knowledge written |
| Power outage and disruption risk | energy | [power_outage_and_disruption_risk.md](../agents/citadel/workflows/power_outage_and_disruption_risk.md) | not written |
| Electric shock hazard | energy | [electric_shock_hazard.md](../agents/citadel/workflows/electric_shock_hazard.md) | not written |
| Rockfall treefall and landslide risk | other | [rockfall_treefall_and_landslide_risk.md](../agents/citadel/workflows/rockfall_treefall_and_landslide_risk.md) | not written |
| Cultural heritage and home flooding risk | other | [cultural_heritage_and_home_flooding_risk.md](../agents/citadel/workflows/cultural_heritage_and_home_flooding_risk.md) | not written |
| River and ocean hazard | other | [river_and_ocean_hazard.md](../agents/citadel/workflows/river_and_ocean_hazard.md) | not written |

**Every card is written. Only one flow has evidence behind it.** A card is the
procedure; it deliberately holds no figures, so writing one settles how an
analysis is done without settling what it knows. A flow marked *not written*
loads, overlays and runs -- and then stops at the first retrieval, because its
sources note does not exist. That is the designed outcome: the rule in
Citadel's system prompt is to say so and stop rather than supply a figure from
memory, and an honest refusal beats an unsourced analysis.

`core.spec.flow_menu` marks those flows `[STUB -- procedure only, no evidence
note yet]` in Argos' routing menu, so the gap is visible **before** a
specialist call is spent discovering it.

Writing a flow's evidence is therefore the whole remaining job, and it is
research rather than engineering: real sources, real figures, each with its
vintage, cited short in the knowledge note and expanded in the sources note.
`Human health risk` is the worked example of what that takes.

Skeletons for all of them are in the vault under
`Hand/Workflows/Chanakya flows/`, catalogued by
`Chanakya flows - MOC.md`.

### The two paths

Citadel answers in one of two ways, and Argos decides which:

- **General question** - answered from the session so far. Runs no queries and
  fetches nothing. If the session cannot answer it, the reply says so and
  offers the flow that would.
- **Analysis** - one of the flows above. Citadel autonomously runs a set of
  queries.

---

## Input sources

`inputs_required` on a flow is an array of these integers. **Numbers are
permanent**: once assigned, never reused and never renumbered, because flows
cite them. Append new sources at the end.

### Wired - a code path exists today

| # | Source | Reached through |
|---|---|---|
| 1 | Weather and soil moisture | `fetch_weather_data` |
| 2 | Land-cover indices | `fetch_lulc_data` |
| 3 | Elevation and slope | `fetch_terrain_data` |
| 4 | Permanent and seasonal water | `fetch_permanent_water` |
| 5 | Session context | `active_context` from Argos |
| 6 | Vault knowledge notes | MCP retrieval |

### Named, not wired - nothing fetches these yet

| # | Source | Candidate provider |
|---|---|---|
| 7 | Wastewater treatment plants and outfalls | HydroWASTE |
| 8 | INFORM Risk, vulnerability and climate-change vulnerability | INFORM / JRC |
| 9 | Population density and distribution | TOTAG |
| 10 | Building footprints and building use | TOTAG |
| 11 | Road network and travel time | TOTAG |
| 12 | Health facilities and emergency services | TOTAG |
| 13 | Electricity network and generation assets | TOTAG |
| 14 | Cooking-fuel and LPG/pipeline distribution | TOTAG |
| 15 | Drinking-water supply and WASH service levels | WHO/UNICEF JMP |
| 16 | Humanitarian and government event data for the AOI | HDX, national agencies |
| 17 | Landslide and slope-instability inventories | TOTAG |
| 18 | Cultural and heritage asset registers | TOTAG |
| 19 | River gauge, discharge and coastal sea-level data | TOTAG |
| 20 | WHO, UN-Water and UNEP guidance and standards | WHO, UN-Water, UNEP |

**No flow in this catalogue can run end to end today.** Every one of them
needs at least one source from the second table. `human_health_risk` needs
7, 8, 9, 15, 16 and 20 - six unwired sources - so it is a written analysis
procedure, not an executable one.

---

## Adding a flow

1. Write the **card**: frontmatter (with `card_note`) in
   `agents/citadel/workflows/<name>.md`, and the `## Flow Card` section in a
   `type: flow-card` note under `Heart/Flow cards/Chanakya/`. Procedure only -- **no figures, no references**.
   Name the `knowledge_note` and the `sources_note` in the frontmatter.
2. Write the **knowledge** in the vault note of the same title. Figures,
   thresholds and case studies, each cited short -- `(WHO 2024)` -- and each
   with its vintage.
3. Write the **sources** note, `<Title> - sources.md`, expanding every citation
   key into a full reference with a vintage and a verification status. This is
   the note that ships to the reader.
4. Add each source to [SOURCES.md](SOURCES.md) as an audit entry -- key,
   vintage, verification, what it is used for. Do not restate the full
   reference there; the vault sources note is the one home for that.
5. Declare `inputs_required` using the numbers above, and add any new source to
   that table **before** the card cites it.
6. Move the vault note's `status` on from `stub`.
7. **Add it to the catalogue table above.**
8. Check the size of the `## Flow Card` section -- that is what gets loaded. If
   it is past roughly 1,000 tokens, evidence has leaked into it; move that back
   to the knowledge note.


