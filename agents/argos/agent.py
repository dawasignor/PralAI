"""Agent Argus - Orchestrator -- thin runner.

Behaviour lives in agents/argos/agents.md (frontmatter + system prompt).
This file only binds that definition to the shared loop in core/base_agent.py.

    python -m agents.argos.agent "plan the flood-mask dataset selection"
"""

from __future__ import annotations

from core.base_agent import BaseAgent

SLUG = "argos"


class Argos(BaseAgent):
    slug = SLUG


def build() -> Argos:
    """Factory used by core/graph.py and run.py."""
    return Argos()


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) or "plan the flood-mask dataset selection"
    agent = build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))
