from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_captured_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "captured_openai_calls", default=None
)


def begin_usage_capture() -> Token:
    """Start request-local OpenAI usage collection.

    The mutable list is intentionally shared with worker-thread context copies so
    usage recorded inside FastAPI's thread pool remains visible to the request.
    """
    return _captured_calls.set([])


def finish_usage_capture(token: Token) -> dict[str, Any]:
    calls = list(_captured_calls.get() or [])
    _captured_calls.reset(token)
    input_tokens = sum(int(call["input_tokens"]) for call in calls)
    output_tokens = sum(int(call["output_tokens"]) for call in calls)
    estimated_usd = sum(float(call["estimated_usd"]) for call in calls)
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd": round(estimated_usd, 8),
    }


def record_openai_usage(
    *,
    operation: str,
    model: str,
    response: Any,
    image_count: int = 0,
) -> None:
    calls = _captured_calls.get()
    if calls is None:
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    web_search_calls = sum(
        1 for item in (getattr(response, "output", None) or [])
        if getattr(item, "type", None) == "web_search_call"
    )
    # Current standard rates per 1M tokens. Raw token counts are retained
    # so historical costs can be recalculated if model prices change.
    if model.startswith("gpt-5-nano"):
        input_rate, output_rate = 0.05, 0.40
    elif model.startswith("gpt-5.6-luna"):
        input_rate, output_rate = 0.20, 1.20
    elif model.startswith("gpt-5.4-nano"):
        input_rate, output_rate = 0.20, 1.25
    else:
        input_rate, output_rate = 0.75, 4.50
    input_cost_usd = input_tokens * input_rate / 1_000_000
    output_cost_usd = output_tokens * output_rate / 1_000_000
    web_search_cost_usd = web_search_calls * 0.01
    estimated_usd = input_cost_usd + output_cost_usd + web_search_cost_usd
    calls.append(
        {
            "operation": operation,
            "stage": operation,
            "model": model,
            "image_count": image_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "web_search_calls": web_search_calls,
            "input_cost_usd": round(input_cost_usd, 8),
            "output_cost_usd": round(output_cost_usd, 8),
            "web_search_cost_usd": round(web_search_cost_usd, 8),
            "estimated_usd": round(estimated_usd, 8),
        }
    )
