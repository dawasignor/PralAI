"""Agent Norwood - Satellite expert -- thin runner.

Behaviour lives in agents/norwood/agents.md (frontmatter + system prompt).
This file only binds that definition to the shared loop in core/base_agent.py.

    python -m agents.norwood.agent "compare Sentinel-1 GRD and SLC for flood extent"
"""

from __future__ import annotations

from core.base_agent import BaseAgent

SLUG = "norwood"


class Norwood(BaseAgent):
    slug = SLUG


def build() -> Norwood:
    """Factory used by core/graph.py and run.py."""
    return Norwood()


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) or "compare Sentinel-1 GRD and SLC for flood extent"
    agent = build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))
