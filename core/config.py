"""Environment-backed configuration.

Every knob lives in .env. Agents ask for a *model slot* (1 or 2), never for a
provider or a model id, so the model can be changed by editing .env alone.

OpenRouter is the only provider. Adding another OpenAI-compatible gateway means
a name in KNOWN_PROVIDERS and a default base URL in DEFAULT_BASE_URLS. Nothing
in agents/ ever changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"

# Root .env first, then an agent-local .env may override it (see load_agent_env).
load_dotenv(REPO_ROOT / ".env")

KNOWN_PROVIDERS = ("openrouter",)

DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass(frozen=True)
class ModelSlot:
    """One configured model, resolved from MODEL_<n>_* / LLM_MODEL_<n>."""

    slot: int
    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    max_tokens: int
    effort: str
    reasoning: bool              # send OpenRouter's reasoning.effort
    extra_headers: dict[str, str]
    # Sampling temperature, sent on every call; None sends nothing (the
    # provider's own default). 1.0 unless MODEL_<n>_TEMPERATURE says otherwise
    # (user decision 2026-11-11: DeepSeek's recommended value, stated rather
    # than left to whichever provider serves the call).
    temperature: float | None = 1.0
    # OpenRouter provider routing. Empty `provider_order` = OpenRouter picks
    # per request (the old behaviour). Set, it pins those providers in that
    # order, which also turns OpenRouter's sticky routing off.
    provider_order: tuple[str, ...] = ()
    allow_fallbacks: bool = True
    quantizations: tuple[str, ...] = ()

    def provider_routing(self) -> dict | None:
        """OpenRouter's `provider` object, or None when nothing is pinned."""
        out: dict = {}
        if self.provider_order:
            out["order"] = list(self.provider_order)
            out["allow_fallbacks"] = self.allow_fallbacks
            # A pinned provider that silently ignored the temperature would
            # break the very setting the pin exists to hold fixed.
            out["require_parameters"] = True
        if self.quantizations:
            out["quantizations"] = list(self.quantizations)
        return out or None

    @property
    def default_base_url(self) -> str:
        return DEFAULT_BASE_URLS.get(self.provider, "")


def env_str(name: str, default: str = "") -> str:
    value = (os.getenv(name) or "").strip()
    # python-dotenv strips an inline `# comment` only when the value is
    # non-empty; on a blank value the comment itself becomes the value. No
    # setting here is legitimately a comment, so treat that as unset.
    if value.startswith("#"):
        value = ""
    return value or default.strip()


def _auto_flag(name: str, auto_default: bool) -> bool:
    """Read a tri-state env flag: "1" / "0" / unset-or-"auto"."""
    raw = env_str(name, "auto").lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return auto_default


def get_model_slot(slot: int = 1) -> ModelSlot:
    """Read MODEL_<slot>_* out of the environment."""
    provider = env_str(f"MODEL_{slot}_PROVIDER", "openrouter").lower()
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(
            f"MODEL_{slot}_PROVIDER={provider!r} is not one of {KNOWN_PROVIDERS}"
        )
    model = env_str(f"LLM_MODEL_{slot}") or "deepseek/deepseek-v4-pro"

    # One account usually serves every slot, so slot 2+ inherits slot 1's key
    # when it points at the same provider. Set MODEL_<n>_API_KEY to override.
    api_key = env_str(f"MODEL_{slot}_API_KEY")
    if not api_key and slot != 1 and provider == env_str("MODEL_1_PROVIDER",
                                                        "openrouter").lower():
        api_key = env_str("MODEL_1_API_KEY")

    headers: dict[str, str] = {}
    if provider == "openrouter":
        # Optional attribution on openrouter.ai; harmless when unset.
        if referer := env_str("OPENROUTER_APP_URL"):
            headers["HTTP-Referer"] = referer
        if title := env_str("OPENROUTER_APP_TITLE"):
            headers["X-Title"] = title

    return ModelSlot(
        slot=slot,
        provider=provider,
        model=model,
        api_key=api_key or None,
        base_url=env_str(f"MODEL_{slot}_BASE_URL") or None,
        max_tokens=int(env_str(f"MODEL_{slot}_MAX_TOKENS", "16000")),
        effort=env_str(f"MODEL_{slot}_EFFORT", "high"),
        # Off unless asked for: reasoning tokens count against max_tokens on
        # this endpoint, so sending an effort is a choice.
        reasoning=_auto_flag(f"MODEL_{slot}_REASONING", False),
        extra_headers=headers,
        temperature=_temperature(slot),
        provider_order=_csv(f"MODEL_{slot}_PROVIDER_ORDER"),
        # A pin with fallbacks on is a preference, not a pin: off unless asked.
        allow_fallbacks=(_auto_flag(f"MODEL_{slot}_ALLOW_FALLBACKS", False)
                         if _csv(f"MODEL_{slot}_PROVIDER_ORDER") else True),
        quantizations=_csv(f"MODEL_{slot}_QUANTIZATIONS"),
    )


def _csv(name: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in env_str(name).split(",") if p.strip())


def _temperature(slot: int) -> float | None:
    """MODEL_<n>_TEMPERATURE: a number, or `none` / `provider` to send nothing.
    Unset is 1.0. A value OpenRouter would refuse is refused here, at start."""
    raw = env_str(f"MODEL_{slot}_TEMPERATURE", "1.0").lower()
    if raw in ("none", "provider", "default"):
        return None
    value = float(raw)
    if not 0.0 <= value <= 2.0:
        raise ValueError(f"MODEL_{slot}_TEMPERATURE={raw!r} is outside 0-2")
    return value


def prompt_run_token() -> bool:
    """PROMPT_RUN_TOKEN=1: open every system prompt with the run id, so no two
    runs share a prompt prefix and no provider cache serves one run from
    another (RQ5 independence; `scripts/run_n.py` sets it for a batch)."""
    return env_str("PROMPT_RUN_TOKEN", "0") == "1"


def load_agent_env(agent_slug: str) -> None:
    """Layer agents/<slug>/.env on top of the root .env, if it exists."""
    agent_env = AGENTS_DIR / agent_slug / ".env"
    if agent_env.exists():
        load_dotenv(agent_env, override=True)


def graph_max_steps() -> int:
    return int(env_str("GRAPH_MAX_STEPS", "8"))


def monty_max_retries() -> int:
    """How many times Argos may re-send a failed job to Monty.

    2 means three attempts in total: the original plus two retries. Past that
    the error itself becomes the final output.
    """
    return int(env_str("MONTY_MAX_RETRIES", "2"))


def memory_enabled() -> bool:
    return env_str("MEMORY_WRITE", "1") == "1"
