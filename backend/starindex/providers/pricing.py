"""Cost estimates for cloud model usage.

An estimate is only produced when a price is known for the model:

* user-entered prices from Settings (``AppSettings.pricing``) always win;
* built-in defaults exist **only** for Anthropic, taken from Anthropic's published price list
  (snapshot dated 2026-11-03). No OpenAI, Gemini or Azure prices are bundled, because none
  could be verified when this was written; add them in Settings to see estimates.

Without a price the cost is ``None`` and the UI shows token counts only. ChatGPT plan usage
is never priced per token (it draws on the user's plan).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings_store import ModelPrice
from .base import Usage

ANTHROPIC_PRICE_SNAPSHOT = "Anthropic price list, snapshot 2026-11-03"


@dataclass(frozen=True)
class _Builtin:
    provider_kind: str
    model_prefix: str
    input_per_mtok: float
    output_per_mtok: float
    # Optional long-prompt tier: above `tier_threshold` input tokens the tier rates apply.
    tier_threshold: int | None = None
    tier_input_per_mtok: float | None = None
    tier_output_per_mtok: float | None = None


# USD per million tokens (standard, non-cached, non-batch, first-party API rates).
_BUILTIN: tuple[_Builtin, ...] = (
    _Builtin("anthropic", "claude-fable-5-2", 8.00, 40.00),
    _Builtin("anthropic", "claude-mythos-5-2", 8.00, 40.00),
    _Builtin("anthropic", "claude-fable-5-1", 10.00, 50.00),
    _Builtin("anthropic", "claude-mythos-5-1", 10.00, 50.00),
    _Builtin("anthropic", "claude-fable-5", 10.00, 50.00),
    _Builtin("anthropic", "claude-opus-5-5", 4.00, 20.00),
    _Builtin("anthropic", "claude-opus-5", 5.00, 25.00),
    _Builtin("anthropic", "claude-opus-4-8", 5.00, 25.00),
    _Builtin("anthropic", "claude-opus-4-7", 5.00, 25.00),
    _Builtin("anthropic", "claude-opus-4-6", 5.00, 25.00),
    _Builtin("anthropic", "claude-sonnet-5-5", 2.00, 10.00),
    _Builtin("anthropic", "claude-sonnet-5", 2.00, 10.00),
    _Builtin("anthropic", "claude-sonnet-4-6", 3.00, 15.00),
    _Builtin("anthropic", "claude-haiku-5-5", 0.10, 0.50, 100_000, 0.50, 2.50),
    _Builtin("anthropic", "claude-haiku-4-5", 1.00, 5.00),
)

PRICING_NOTE = (
    "Costs are estimates. Built-in prices cover Anthropic models only ("
    + ANTHROPIC_PRICE_SNAPSHOT
    + "); add prices for other providers in Settings to see estimates for them. "
    "Cached-token discounts and batch pricing are not modelled. ChatGPT plan usage is not priced per token."
)


def builtin_prices() -> list[ModelPrice]:
    """The bundled defaults in the user-facing shape (for display in Settings)."""
    return [
        ModelPrice(
            provider_kind=b.provider_kind,
            model_prefix=b.model_prefix,
            input_per_mtok=b.input_per_mtok,
            output_per_mtok=b.output_per_mtok,
            source=ANTHROPIC_PRICE_SNAPSHOT,
        )
        for b in _BUILTIN
    ]


def _best(candidates: list[tuple[str, object]], model: str) -> object | None:
    """Longest matching prefix wins (so ``claude-opus-5-5`` beats ``claude-opus-5``)."""
    name = model.lower()
    matches = [(prefix, item) for prefix, item in candidates if name.startswith(prefix.lower())]
    if not matches:
        return None
    return max(matches, key=lambda pair: len(pair[0]))[1]


def find_price(user_prices: list[ModelPrice], provider_kind: str, model: str) -> ModelPrice | _Builtin | None:
    user = _best([(p.model_prefix, p) for p in user_prices if p.provider_kind == provider_kind and p.model_prefix], model)
    if user is not None:
        return user  # type: ignore[return-value]
    return _best([(b.model_prefix, b) for b in _BUILTIN if b.provider_kind == provider_kind], model)  # type: ignore[return-value]


def estimate_cost(user_prices: list[ModelPrice], provider_kind: str, model: str, usage: Usage) -> float | None:
    """USD estimate, or ``None`` when no price is known or no token counts were reported."""
    if provider_kind == "chatgpt_plan":
        return None
    if usage.input_tokens is None and usage.output_tokens is None:
        return None
    price = find_price(user_prices, provider_kind, model)
    if price is None:
        return None
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    input_rate, output_rate = price.input_per_mtok, price.output_per_mtok
    if isinstance(price, _Builtin) and price.tier_threshold is not None and input_tokens > price.tier_threshold:
        input_rate = price.tier_input_per_mtok or input_rate
        output_rate = price.tier_output_per_mtok or output_rate
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
