"""Loader for an agent's definition: runtime settings in the repo, prompts in the vault.

Two halves with two owners:

* `agents/<slug>/agents.md` -- YAML frontmatter holds the runtime config
  (model slot, token ceiling, tools, turn budget) and `prompt_note`, the title
  of the vault note holding the prompts. `agents/<slug>/workflows/<flow>.md`
  holds a flow's routing and input frontmatter and `card_note`, the title of
  its card. These are what the code acts on, so they stay where a test and a
  commit see them.
* The vault -- the prompt text itself: `## System Prompt`, and for Argus
  `## Resolver Prompt` and `## Debug Prompt`, in a `type: agent-prompt` note;
  each flow's `## Flow Card` in a `type: flow-card` note. The user writes them
  in Obsidian.

**A prompt note is an instruction, every other note is data.** core/vault.py
serves notes as data and says so in every read. This module is the one
exception, and it is narrow on purpose: the note must sit under `Heart/`, carry
the right `type`, and name this agent (and this flow) in its frontmatter.
Anything else is refused, so a dataset note pasted from the web can never
become a prompt by being given the right title.

**Missing means stop, not degrade.** An agent with an empty or partial prompt
still answers, and the answer looks like any other. So every failure here
raises PromptNoteError naming the note, and `check_all()` runs at start-up
(`run.py --list`, the console server) to fail before the first message.

**Every prompt carries a hash.** `prompt_shas` / `card_sha` are the first 12
hex digits of the SHA-256 of the loaded text. An edit in Obsidian changes
behaviour without a commit in this repo, so the agent-call event records which
version ran; a thesis figure can then be traced to the prompt that produced it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import AGENTS_DIR

# The only vault folder a prompt may be loaded from (R1 in the plan).
PROMPT_FOLDER = "Heart"
AGENT_PROMPT = "agent-prompt"
FLOW_CARD = "flow-card"


class PromptNoteError(ValueError):
    """A prompt note is missing, ambiguous, outside Heart/, or not the note
    it claims to be. Raised, never degraded: see the module docstring."""


def prompt_sha(text: str) -> str:
    """First 12 hex digits of the SHA-256 of a prompt, for the run record."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class FlowSpec:
    """One analysis an agent can run, loaded from agents/<slug>/workflows/.

    A flow is an overlay on an agent, not an agent: the same Chanakya, pointed
    at one analysis. `card` is the procedure and is the only part that reaches
    a prompt -- the evidence lives in the vault and is retrieved per step, and
    the citations live in docs/SOURCES.md and never reach a prompt at all.
    """

    name: str                    # file stem, e.g. "human_health_risk"
    title: str
    select_when: str             # one line Argus routes on
    inputs_required: list[int]
    # Cached analysis payloads this flow feeds its agent, by name under
    # `experiments/<event>/inputs/`. The graph assembles these and puts them IN
    # the prompt: the agent acts on data the flow produced for it rather than
    # going to find data itself. Norwood spent its whole turn budget fetching
    # three payloads by tool call and ran out before writing anything, which is
    # what this exists to stop.
    consumes: list[str]
    # The output contract this flow's agent emits, e.g.
    # "norwood/pre-flood-aoi-brief". `core/projection.py` keys the field
    # entitlement off it, so a flow that declares no schema is served the
    # statistics whole rather than narrowed by a projection meant for
    # something else.
    consumes_schema: str
    knowledge_note: str          # vault note holding the evidence
    sources_note: str            # vault note expanding every citation key,
                                 # "" when the flow retrieves nothing
    status: str                  # "ready" once the evidence note is written
    card: str                    # the '## Flow Card' section of `card_note`
    # A Monty workflow whose result this flow interprets, "" for none. When that
    # workflow succeeds, Argus is told to route here before answering -- one
    # interpretation hop, in the same run. `accuracy_metrics` -> Entro's
    # `accuracy_interpretation` is the case: the table is Monty's, the reading
    # of it is Entro's, and the user asked for both in one question.
    after_workflow: str = ""
    # Every workflow whose result this flow claims (`after_workflow` may be a
    # list); `after_workflow` keeps the first, for readers of one name.
    after_workflows: list = field(default_factory=list)
    # A flow that WRITES a module (Modules 9 and 10, user decision 2026-11-02):
    # the specialist node runs the module's steps (core/pipelines/hev.py) on
    # the session store instead of answering in free prose.
    module: str = ""
    # The specialist's reply goes under Argus' answer as written
    # (`append_to_answer: true`): Norwood's comments on a result just fetched.
    append_to_answer: bool = False
    card_note: str = ""          # vault title of the card
    card_sha: str = ""           # prompt_sha(card)


@dataclass(frozen=True)
class AgentSpec:
    slug: str
    name: str
    role: str
    model_slot: int
    max_tokens: int | None
    effort: str | None
    tools: list[str]
    max_turns: int
    system_prompt: str
    debug_prompt: str            # optional '## Debug Prompt' section, "" if absent
    resolver_prompt: str         # optional '## Resolver Prompt' section, "" if absent
    source: Path
    prompt_note: str = ""        # vault title the prompts came from
    # prompt_sha of each section loaded, keyed system / resolver / debug.
    prompt_shas: dict[str, str] = field(default_factory=dict)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm) or {}, body


def _section(body: str, heading: str) -> str:
    """Return the text under `## <heading>`, up to the next `##` heading.

    Matches the heading only when it is a real heading line. These files talk
    *about* their own sections in prose (Argus' system prompt mentions
    `## Debug Prompt`), and a naive `str.split` picks up that mention instead
    -- silently returning the wrong prompt rather than raising.
    """
    start = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.MULTILINE)
    if not start:
        return ""
    rest = body[start.end():]
    end = re.search(r"^##\s+\S", rest, re.MULTILINE)
    return (rest[: end.start()] if end else rest).strip()


def _prompt_note(title: str, kind: str, agent_slug: str,
                 flow_name: str = "") -> tuple[str, Path]:
    """The body of the vault note `title`, checked to be the prompt it claims.

    Read through the process-wide vault index, so every agent in a run agrees
    on which file a title names. The file itself is read fresh on every call:
    an edit made in Obsidian reaches the next agent call, and its hash says so.
    """
    from . import vault

    who = f"agent {agent_slug!r}" + (f", flow {flow_name!r}" if flow_name else "")
    if not title:
        raise PromptNoteError(f"no {kind} note named for {who}")
    where = f"{kind} note {title!r} ({who})"
    try:
        index = vault.shared_index()
        path = vault.resolve(index, title)
    except vault.VaultError as exc:
        raise PromptNoteError(f"cannot load the {where}: {exc}") from exc

    rel = path.relative_to(index.root)
    if rel.parts[0] != PROMPT_FOLDER:
        raise PromptNoteError(f"refusing the {where}: it is at {rel}, and prompts "
                              f"load only from {PROMPT_FOLDER}/")
    try:
        meta, body = vault.split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise PromptNoteError(f"cannot read the {where}: {exc}") from exc
    if meta.get("type") != kind:
        raise PromptNoteError(f"refusing the {where}: its type is "
                              f"{meta.get('type')!r}, not {kind!r}")
    if str(meta.get("agent") or "") != agent_slug:
        raise PromptNoteError(f"refusing the {where}: it names agent "
                              f"{meta.get('agent')!r}")
    if flow_name and str(meta.get("flow") or "") != flow_name:
        raise PromptNoteError(f"refusing the {where}: it names flow "
                              f"{meta.get('flow')!r}")
    return body, path


def load_spec(agent_slug: str) -> AgentSpec:
    path = AGENTS_DIR / agent_slug / "agents.md"
    if not path.exists():
        raise FileNotFoundError(f"no agents.md for {agent_slug!r} at {path}")

    meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    note = str(meta.get("prompt_note") or "").strip()
    body, note_path = _prompt_note(note, AGENT_PROMPT, agent_slug)
    prompt = _section(body, "System Prompt")
    if not prompt:
        raise PromptNoteError(f"{note_path} has no '## System Prompt' section")
    debug, resolver = _section(body, "Debug Prompt"), _section(body, "Resolver Prompt")
    shas = {"system": prompt_sha(prompt)}
    if resolver:
        shas["resolver"] = prompt_sha(resolver)
    if debug:
        shas["debug"] = prompt_sha(debug)

    return AgentSpec(
        slug=meta.get("slug", agent_slug),
        name=meta.get("name", agent_slug.title()),
        role=meta.get("role", ""),
        model_slot=int(meta.get("model_slot", 1)),
        max_tokens=meta.get("max_tokens"),
        effort=meta.get("effort"),
        tools=list(meta.get("tools") or []),
        max_turns=int(meta.get("max_turns", 6)),
        system_prompt=prompt,
        # A second persona the same agent can be put into -- Argus uses it to
        # become a debugger after a Monty failure. Optional; "" when absent.
        debug_prompt=debug,
        # The overlay that turns the agent into a reader of places and dates
        # for one call, before any routing happens.
        resolver_prompt=resolver,
        source=path,
        prompt_note=note,
        prompt_shas=shas,
    )


def flows_dir(agent_slug: str):
    return AGENTS_DIR / agent_slug / "workflows"


def _after(meta: dict) -> list[str]:
    """`after_workflow` as a list: one name, or several (a flow that comments
    on any of several results, e.g. Norwood's event_commentary)."""
    raw = meta.get("after_workflow") or []
    items = raw if isinstance(raw, list) else [raw]
    return [str(i).strip() for i in items if str(i).strip()]


def load_flow(agent_slug: str, flow_name: str) -> FlowSpec:
    """Load one flow: frontmatter from the repo, card from the vault.

    Raises if the file, the card note or its `## Flow Card` section is missing.
    """
    path = flows_dir(agent_slug) / f"{flow_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no flow {flow_name!r} for {agent_slug!r} at {path}")

    meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    note = str(meta.get("card_note") or "").strip()
    body, note_path = _prompt_note(note, FLOW_CARD, agent_slug,
                                   str(meta.get("flow", flow_name)))
    card = _section(body, "Flow Card")
    if not card:
        raise PromptNoteError(f"{note_path} has no '## Flow Card' section")

    return FlowSpec(
        name=meta.get("flow", flow_name),
        title=meta.get("title", flow_name),
        select_when=meta.get("select_when", ""),
        inputs_required=[int(i) for i in (meta.get("inputs_required") or [])],
        consumes=[str(c).strip() for c in (meta.get("consumes") or []) if str(c).strip()],
        consumes_schema=str(meta.get("consumes_schema", "")).strip(),
        knowledge_note=meta.get("knowledge_note", ""),
        sources_note=meta.get("sources_note", ""),
        status=str(meta.get("status", "ready")),
        card=card,
        after_workflow=_after(meta)[0] if _after(meta) else "",
        after_workflows=_after(meta),
        module=str(meta.get("module", "") or "").strip(),
        append_to_answer=bool(meta.get("append_to_answer", False)),
        card_note=note,
        card_sha=prompt_sha(card),
    )


def list_flows(agent_slug: str) -> list[FlowSpec]:
    """Every flow an agent has, for the routing menu.

    Only `select_when` and the title are needed to route, so a caller building
    Argus' menu reads those and never loads eleven cards into one prompt.
    """
    directory = flows_dir(agent_slug)
    if not directory.is_dir():
        return []
    return [load_flow(agent_slug, p.stem) for p in sorted(directory.glob("*.md"))]


def flow_menu(agent_slug: str) -> str:
    """One line per flow -- goes into Argus' routing prompt.

    Only the name and `select_when`. The whole point of the card/knowledge
    split is that eleven cards never enter one prompt; a routing decision needs
    the trigger, not the procedure.

    A stub is marked here rather than hidden. Hiding it means a user asking for
    that analysis is routed somewhere else without being told it exists;
    marking it lets Argus say "written, but not yet evidenced" and spend no
    specialist call discovering that.
    """
    lines = []
    for flow in list_flows(agent_slug):
        if not flow.select_when:
            continue
        mark = " [STUB -- procedure only, no evidence note yet]" \
            if flow.status == "stub" else ""
        lines.append(f"- {flow.name} -- {flow.select_when}{mark}")
    return "\n".join(lines)


def valid_flow(agent_slug: str, flow_name: str) -> str:
    """The flow name if that agent really has it, otherwise "".

    Argus chooses; this only checks the name exists, the same way an unknown
    workflow name is rejected rather than invented. An unrecognised flow
    degrades to the agent's general path instead of failing the hop.
    """
    name = (flow_name or "").strip()
    if not name:
        return ""
    return name if name in {f.name for f in list_flows(agent_slug)} else ""


def agent_slugs() -> list[str]:
    """Every agent with an agents.md, in folder order."""
    return sorted(p.parent.name for p in AGENTS_DIR.glob("*/agents.md"))


def check_all() -> list[str]:
    """Load every agent's prompts and every flow card, or raise.

    Run at start-up so a missing or refused note stops the process before the
    first message, rather than one node at a time mid-conversation. Returns
    one line per prompt loaded, for the start-up log.
    """
    lines = []
    for slug in agent_slugs():
        spec = load_spec(slug)
        lines.append(f"{slug}: {spec.prompt_note} "
                     + " ".join(f"{k}={v}" for k, v in spec.prompt_shas.items()))
        for flow in list_flows(slug):
            lines.append(f"{slug}/{flow.name}: {flow.card_note} card={flow.card_sha}")
    return lines


def token_report() -> list[dict]:
    """Approximate prompt size per agent and mode, at 4 characters a token.

    Reported, never enforced or trimmed (R4 in the plan): a prompt cut to fit a
    budget would lose its last rules silently. A flow row is the card alone; at
    run time it is added to the system prompt of its agent.
    """
    rows = []
    for slug in agent_slugs():
        spec = load_spec(slug)
        for mode, text in (("system", spec.system_prompt),
                           ("resolver", spec.resolver_prompt),
                           ("debug", spec.debug_prompt)):
            if text:
                rows.append({"agent": slug, "mode": mode, "tokens": len(text) // 4})
        for flow in list_flows(slug):
            rows.append({"agent": slug, "mode": f"flow:{flow.name}",
                         "tokens": len(flow.card) // 4})
    return rows
