"""Monty - Python expert -- thin runner.

Behaviour lives in agents/monty/agents.md (frontmatter + system prompt).
This file only binds that definition to the shared loop in core/base_agent.py.

    python -m agents.monty.agent "vectorise this reprojection loop"
"""

from __future__ import annotations

from core.base_agent import BaseAgent

SLUG = "monty"


class Monty(BaseAgent):
    slug = SLUG


def build() -> Monty:
    """Factory used by core/graph.py and run.py."""
    return Monty()


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) or "vectorise this reprojection loop"
    agent = build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))
