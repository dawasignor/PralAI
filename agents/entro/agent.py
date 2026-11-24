"""Entro - Uncertainty Analyser -- thin runner.

Behaviour lives in agents/entro/agents.md (frontmatter + system prompt).
This file only binds that definition to the shared loop in core/base_agent.py.

    python -m agents.entro.agent "how confident is the 2024 Bangkok flood mask?"
"""

from __future__ import annotations

from core.base_agent import BaseAgent

SLUG = "entro"


class Entro(BaseAgent):
    slug = SLUG


def build() -> Entro:
    """Factory used by core/graph.py and run.py."""
    return Entro()


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) or "how confident is the 2024 Bangkok flood mask?"
    agent = build()
    print(f"[{agent!r}]\n")
    print(agent.run(task))
