"""RQ5 independence settings: temperature, provider pin, run token, served-by.

    python tests/test_independence.py      # no model, no network, no cost

    1. temperature  -- unset is 1.0 and is sent; `none` sends nothing; out of range refused
    2. pin          -- no order: no provider object; an order pins it, fallbacks
                       off, parameters required; fallbacks can be asked for
    3. request      -- the request carries temperature and provider routing;
                       the provider that served it is read back
    4. run token    -- with PROMPT_RUN_TOKEN=1 and a run id, the system prompt
                       STARTS with the run id; off, it does not
    5. recorded     -- metrics.jsonl gets providers and settings per agent call
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import cache_policy, config  # noqa: E402

results: list[tuple[str, str]] = []
KEYS = ("MODEL_9_TEMPERATURE", "MODEL_9_PROVIDER_ORDER", "MODEL_9_ALLOW_FALLBACKS",
        "MODEL_9_QUANTIZATIONS", "MODEL_9_API_KEY", "PROMPT_RUN_TOKEN")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def env(**kw) -> None:
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in kw.items()})


class FakeCompletions:
    def __init__(self):
        self.sent = None

    def create(self, **params):
        self.sent = params
        msg = SimpleNamespace(content="ok", tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, cost=0.001,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=0))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=usage, error=None, provider="DeepInfra")


def fake_client(slot):
    from core.llm import LLMClient

    c = LLMClient.__new__(LLMClient)
    c.slot, c.max_tokens, c.effort = slot, 100, "high"
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    return c


def main() -> int:
    saved = {k: os.environ.get(k) for k in KEYS}
    try:
        # 1. temperature
        env()
        check("temperature: unset is 1.0", config.get_model_slot(9).temperature == 1.0)
        env(MODEL_9_TEMPERATURE="none")
        check("temperature: none sends nothing", config.get_model_slot(9).temperature is None)
        env(MODEL_9_TEMPERATURE="0.3")
        check("temperature: a value is read", config.get_model_slot(9).temperature == 0.3)
        env(MODEL_9_TEMPERATURE="3")
        try:
            config.get_model_slot(9)
            check("temperature: out of range refused", False)
        except ValueError:
            check("temperature: out of range refused", True)

        # 2. pin
        env()
        check("pin: none by default", config.get_model_slot(9).provider_routing() is None)
        env(MODEL_9_PROVIDER_ORDER="deepinfra/fp8, novita/fp8", MODEL_9_QUANTIZATIONS="fp8")
        r = config.get_model_slot(9).provider_routing()
        check("pin: order, no fallbacks, parameters required, precision",
              r == {"order": ["deepinfra/fp8", "novita/fp8"], "allow_fallbacks": False,
                    "require_parameters": True, "quantizations": ["fp8"]}, str(r))
        env(MODEL_9_PROVIDER_ORDER="deepinfra/fp8", MODEL_9_ALLOW_FALLBACKS="1")
        check("pin: fallbacks when asked", config.get_model_slot(9).provider_routing()["allow_fallbacks"])

        # 3. request
        env(MODEL_9_PROVIDER_ORDER="deepinfra/fp8")
        c = fake_client(config.get_model_slot(9))
        resp = c._complete_openai("sys", [{"role": "user", "content": "hi"}], None, 100)
        sent = c._client.chat.completions.sent
        check("request: temperature sent", sent.get("temperature") == 1.0, str(sent.get("temperature")))
        check("request: provider routing sent", (sent.get("extra_body") or {}).get("provider", {}).get("order")
              == ["deepinfra/fp8"], str(sent.get("extra_body")))
        check("request: served-by read back", resp.usage.get("provider") == "DeepInfra")
        env(MODEL_9_TEMPERATURE="none")
        c = fake_client(config.get_model_slot(9))
        c._complete_openai("sys", [], None, 100)
        sent = c._client.chat.completions.sent
        check("request: nothing extra when unset and none",
              "temperature" not in sent and sent.get("extra_body") is None, str(sent.keys()))

        # 4. run token
        from core.base_agent import BaseAgent

        agent = BaseAgent.__new__(BaseAgent)
        agent.slug = "entro"
        agent.spec = SimpleNamespace(system_prompt="PERSONA TEXT")
        env(PROMPT_RUN_TOKEN="1")
        with cache_policy.use(cache_policy.OFF, run="rq5-test-r07"):
            on = agent.system_prompt()
        env(PROMPT_RUN_TOKEN="0")
        with cache_policy.use(cache_policy.OFF, run="rq5-test-r07"):
            off = agent.system_prompt()
        check("run token: prompt starts with the run id", on.startswith("Run: rq5-test-r07"), on[:60])
        check("run token: off leaves the prompt alone", off.startswith("PERSONA TEXT"), off[:40])

        # 5. recorded
        from core import observability

        with tempfile.TemporaryDirectory() as tmp:
            old = cache_policy.RUNS_DIR
            cache_policy.RUNS_DIR = Path(tmp)
            try:
                with cache_policy.use(cache_policy.OFF, run="rq5-test-r08"):
                    observability.record_agent("entro", 1.0, providers=["DeepInfra"],
                                               settings={"temperature": 1.0, "run_token": True})
                files = list(Path(tmp).rglob("metrics.jsonl"))
                row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1]) if files else {}
            finally:
                cache_policy.RUNS_DIR = old
        check("recorded: providers and settings in metrics.jsonl",
              row.get("providers") == ["DeepInfra"] and row.get("settings", {}).get("run_token") is True,
              str(row)[:200])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
