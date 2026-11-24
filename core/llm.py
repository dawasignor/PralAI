"""The one and only place this repo talks to a model provider.

Naming is deliberately generic (llm_model_1 / model_1_api_key) so the model
can be swapped without touching agents.

Provider
--------
OpenRouter, through its OpenAI-compatible ``/v1/chat/completions`` endpoint and
the ``openai`` SDK. Agents see :class:`LLMResponse` and :class:`ToolCall` and
never an SDK type.

Callers must not hand-build follow-up turns. Use
:meth:`LLMClient.append_assistant_turn` and :meth:`LLMClient.append_tool_results`;
the ``messages`` list is opaque to everything outside this file.

The SDK is imported lazily.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import redact
from .config import ModelSlot, get_model_slot

# Normalised stop reasons. finish_reason is mapped onto the vocabulary the rest
# of the repo keys on (end_turn / tool_use / max_tokens / refusal).
_OPENAI_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


@dataclass
class ToolCall:
    """One tool invocation, provider-neutral."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Provider-neutral view of one model turn."""

    text: str
    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: Any = None          # provider-native, used to build the next turn
    usage: dict[str, Any] = field(default_factory=dict)

    def json(self, fallback: dict | None = None) -> dict:
        """Best-effort parse of a JSON object out of `text`.

        Models sometimes wrap JSON in prose or a fence; be forgiving rather
        than crashing the graph.
        """
        blob = self.text.strip()
        if blob.startswith("```"):
            blob = blob.split("```")[1]
            blob = blob[4:] if blob.startswith("json") else blob
        start, end = blob.find("{"), blob.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(blob[start : end + 1])
            except json.JSONDecodeError:
                pass
        return fallback if fallback is not None else {}


def _missing(package: str, provider: str):
    return RuntimeError(
        f"provider {provider!r} needs the {package!r} package: pip install {package}"
    )


def _build_client(slot: ModelSlot):
    """Construct the provider SDK client for this slot."""
    try:
        from openai import OpenAI
    except ImportError as exc:                          # pragma: no cover
        raise _missing("openai", slot.provider) from exc

    if not slot.api_key:
        raise RuntimeError(
            f"MODEL_{slot.slot}_API_KEY is empty -- put your {slot.provider} key "
            "in .env (this is the only setup step left)"
        )
    # Long generations are not streamed on this path, so give the request room
    # rather than letting a slow completion trip the default timeout.
    return OpenAI(
        api_key=slot.api_key,
        base_url=slot.base_url or slot.default_base_url,
        timeout=600.0,
        default_headers=slot.extra_headers or None,
    )


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool definitions (`input_schema`) -> OpenAI function definitions.

    `strict` is deliberately dropped: OpenAI strict mode requires every
    property to appear in `required`, which our schemas (with genuinely
    optional arguments) do not satisfy. Validation still happens in
    `core/tools.py::execute`, which returns an error string to the model.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in tools
    ]


def _served_by(completion: Any) -> str:
    name = getattr(completion, "provider", None)
    if not name and hasattr(completion, "model_dump"):
        try:
            name = completion.model_dump().get("provider")
        except Exception:                                      # noqa: BLE001
            name = None
    return str(name or "")


class LLMClient:
    """Thin wrapper over one model slot.

    Agents call `.complete(...)`; they never see the provider SDK.
    """

    def __init__(self, slot: int = 1, *, max_tokens: int | None = None,
                 effort: str | None = None):
        self.slot = get_model_slot(slot)
        self.max_tokens = max_tokens or self.slot.max_tokens
        self.effort = effort or self.slot.effort
        self._client = _build_client(self.slot)

    # -- public API --------------------------------------------------------
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """One model turn. Returns a provider-neutral LLMResponse.

        Everything sent is redacted first (`redact.prompt_text`): no absolute
        path, home folder or account name of this machine reaches the
        provider. Repo paths go as `<repo>/...`; Monty expands them back.
        """
        return self._complete_openai(redact.prompt_text(system),
                                     redact.prompt_deep(list(messages)), tools,
                                     max_tokens or self.max_tokens)

    def append_assistant_turn(self, messages: list[dict[str, Any]],
                              response: LLMResponse) -> None:
        """Append the model's own turn, in the provider's native shape."""
        # The OpenAI shape is fully described by the neutral fields, so rebuild
        # it rather than depending on the SDK object. That keeps an
        # LLMResponse constructed in a test just as usable as a real one.
        turn: dict[str, Any] = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            turn["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.input),
                    },
                }
                for call in response.tool_calls
            ]
        messages.append(turn)

    def append_tool_results(self, messages: list[dict[str, Any]],
                            results: Iterable[tuple[ToolCall, str]]) -> None:
        """Append tool results for the turn just appended: one `tool` message
        per call."""
        for call, output in results:
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })

    def describe(self) -> str:
        return f"slot {self.slot.slot} | {self.slot.provider}:{self.slot.model}"

    # -- OpenRouter (OpenAI-compatible) ----------------------------------
    def _complete_openai(self, system, messages, tools, budget):
        params: dict[str, Any] = {
            "model": self.slot.model,
            "max_tokens": budget,
            "messages": [{"role": "system", "content": system}] + list(messages),
        }
        if tools:
            params["tools"] = _to_openai_tools(tools)
        if self.slot.temperature is not None:
            params["temperature"] = self.slot.temperature

        extra: dict[str, Any] = {}
        if self.slot.reasoning:
            # Same effort vocabulary as MODEL_<n>_EFFORT, so no mapping needed.
            extra["reasoning"] = {"effort": self.effort}
        if routing := self.slot.provider_routing():
            extra["provider"] = routing

        completion = self._client.chat.completions.create(
            **params, extra_body=extra or None
        )

        # OpenRouter reports an upstream provider failure in the BODY, with an
        # HTTP 200. The SDK therefore raises nothing and returns a completion
        # whose `choices` is None -- and indexing that produced a bare
        # TypeError that took the whole graph down. Worse, it defeated the
        # self-heal loop, which can only act on a failure that was reported as
        # one (non-negotiable 8). Turn it into something a caller can read.
        error = getattr(completion, "error", None)
        if error is None and hasattr(completion, "model_dump"):
            error = completion.model_dump().get("error")
        if error or not completion.choices:
            raise RuntimeError(
                f"{self.slot.provider} returned no completion for "
                f"{self.slot.model}: {error or 'empty choices'}"
            )

        choice = completion.choices[0]
        message = choice.message

        tool_calls = []
        for call in (message.tool_calls or []):
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                # Surfaced to the model as a tool error rather than crashing
                # the loop; core/tools.py::execute reports the bad payload.
                arguments = {"__unparsed_arguments__": call.function.arguments}
            tool_calls.append(
                ToolCall(id=call.id, name=call.function.name, input=arguments)
            )

        usage = completion.usage
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        return LLMResponse(
            text=(message.content or "").strip(),
            stop_reason=_OPENAI_STOP.get(choice.finish_reason, choice.finish_reason or ""),
            tool_calls=tool_calls,
            raw_message=message,
            usage={
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
                "cache_read": getattr(details, "cached_tokens", 0) or 0,
                # OpenRouter's own charge for the call, when the response
                # carries one (read, never requested here). None when it does
                # not: a cost that was not reported is not known, not zero.
                "cost": getattr(usage, "cost", None) if usage else None,
                # Which company served the call: OpenRouter names it in the
                # body. One model id is many providers at several precisions,
                # so this is what shows whether two runs ran on the same weights.
                "provider": _served_by(completion),
            },
        )
