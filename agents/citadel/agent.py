"""Agent Chanakya - City Planner -- thin runner.

Behaviour lives in agents/citadel/agents.md (frontmatter + system prompt).
This file only binds that definition to the shared loop in core/base_agent.py.

    python -m agents.citadel.agent "which drainage upgrade should the ward fund first?"
"""

from __future__ import annotations

from core.base_agent import BaseAgent

SLUG = "citadel"


class Citadel(BaseAgent):
    slug = SLUG


def build() -> Citadel:
    """Factory used by core/graph.py and run.py."""
    return Citadel()


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) or "which drainage upgrade should the ward fund first?"
    agent = build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))
