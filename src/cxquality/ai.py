"""Minimal OpenRouter clients with strict budget enforcement and no secret logging."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from threading import local
from typing import Any

import requests

from .budget import BudgetGuard, BudgetReservation


class OpenRouterError(RuntimeError):
    """A provider request failed. The exception deliberately excludes response content."""


@dataclass(frozen=True)
class OpenRouterConfig:
    decision_model: str = "typesafe/jev-1.14"
    writer_model: str = "openai/gpt-6-luna"
    decision_endpoint: str = "https://openrouter.ai/api/alpha/decisions"
    chat_endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_seconds: float = 90.0
    # Prices are only preflight estimates; provider-reported cost is authoritative.
    decision_input_per_million_usd: float = 0.042
    writer_input_per_million_usd: float = 0.10
    writer_output_per_million_usd: float = 0.50
    writer_max_tokens: int = 450


def _rough_token_count(value: Any) -> int:
    """Conservative token estimate without adding a tokenizer dependency."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Arabic often has fewer characters per token than English, so use 2.5 chars/token.
    return max(1, int(len(text) / 2.5) + 32)


def _usage_cost(response: Mapping[str, Any], fallback: float) -> float:
    usage = response.get("usage") or {}
    try:
        return max(0.0, float(usage.get("cost", fallback)))
    except (TypeError, ValueError):
        return fallback


class OpenRouterClient:
    """Raw HTTPS client for Jev decisions and limited generative writing."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        budget: BudgetGuard | None = None,
        config: OpenRouterConfig | None = None,
        http: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        self.budget = budget or BudgetGuard()
        self.config = config or OpenRouterConfig()
        self._injected_http = http
        self._thread_state = local()

    def _http_client(self) -> Any:
        if self._injected_http is not None:
            return self._injected_http
        client = getattr(self._thread_state, "http", None)
        if client is None:
            client = requests.Session()
            self._thread_state.http = client
        return client

    def reset_thread_cost(self) -> None:
        self._thread_state.cost_usd = 0.0

    def consume_thread_cost(self) -> float:
        cost = float(getattr(self._thread_state, "cost_usd", 0.0))
        self._thread_state.cost_usd = 0.0
        return cost

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mahssan750/gupshup-cx-quality-agent",
            "X-Title": "Gupshup CX Quality Agent",
        }

    def _post(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        reservation: BudgetReservation,
        fallback_cost: float,
    ) -> dict[str, Any]:
        try:
            response = self._http_client().post(
                endpoint,
                headers=self.headers,
                json=dict(payload),
                timeout=self.config.timeout_seconds,
            )
        except Exception as exc:  # requests and test transports expose different exceptions
            self.budget.cancel(reservation)
            raise OpenRouterError("OpenRouter request failed before a response was received") from exc
        if not 200 <= int(response.status_code) < 300:
            self.budget.cancel(reservation)
            raise OpenRouterError(f"OpenRouter returned HTTP {response.status_code}")
        try:
            data = response.json()
        except Exception as exc:
            self.budget.cancel(reservation)
            raise OpenRouterError("OpenRouter returned invalid JSON") from exc
        actual_cost = _usage_cost(data, fallback_cost)
        self.budget.commit(reservation, actual_cost)
        self._thread_state.cost_usd = (
            float(getattr(self._thread_state, "cost_usd", 0.0)) + actual_cost
        )
        return data

    def decide(self, state: str | Mapping[str, Any], questions: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.config.decision_model,
            "state": state,
            "questions": dict(questions),
        }
        estimated_tokens = _rough_token_count(payload)
        estimated_cost = estimated_tokens * self.config.decision_input_per_million_usd / 1_000_000
        # Small multiplier covers envelope/tokenization differences while remaining inexpensive.
        reservation = self.budget.reserve(estimated_cost * 1.35, "Jev decision")
        return self._post(
            self.config.decision_endpoint,
            payload,
            reservation,
            reservation.estimated_cost_usd,
        )

    def write_json(
        self,
        *,
        task: str,
        context: Mapping[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        max_tokens = max_tokens or self.config.writer_max_tokens
        system = (
            "Return one compact JSON object only. Do not invent facts. Do not repeat customer "
            "names, phone numbers, email addresses, order numbers, or other identifiers. If the "
            "requested output is unsupported by the supplied context, say so in the JSON."
        )
        user = json.dumps({"task": task, "context": context}, ensure_ascii=False)
        payload = {
            "model": self.config.writer_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        in_tokens = _rough_token_count(system + user)
        estimated_cost = (
            in_tokens * self.config.writer_input_per_million_usd
            + max_tokens * self.config.writer_output_per_million_usd
        ) / 1_000_000
        reservation = self.budget.reserve(estimated_cost * 1.25, "generative helper")
        data = self._post(
            self.config.chat_endpoint,
            payload,
            reservation,
            reservation.estimated_cost_usd,
        )
        try:
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OpenRouterError("Writer response did not contain the required JSON object") from exc
        if not isinstance(result, dict):
            raise OpenRouterError("Writer response JSON must be an object")
        return result

    def clarification_question(
        self,
        *,
        transcript_excerpt: str,
        uncertainty: str,
        known_policies: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.write_json(
            task=(
                "Write one short, specific question for the manager that would make this support "
                "conversation scorable. Return keys question, knowledge_gap, and suggested_action "
                "where suggested_action is answer or ignore."
            ),
            context={
                "transcript_excerpt": transcript_excerpt,
                "uncertainty": uncertainty,
                "known_policies": known_policies or [],
            },
            max_tokens=240,
        )

    def normalize_alias(self, alias: str, candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
        return self.write_json(
            task=(
                "Normalize the chat agent alias into a clear English display name and optionally "
                "select one supplied roster candidate. Do not merge unless supported. Return "
                "canonical_name_en, candidate_key or null, confidence from 0 to 1, and reason."
            ),
            context={"alias": alias, "candidates": candidates},
            max_tokens=240,
        )

    def coaching(self, evaluation: Mapping[str, Any]) -> dict[str, Any]:
        return self.write_json(
            task=(
                "Create concise coaching in the conversation language. Return summary, strengths "
                "(array), improvements (array), and better_response. Use only supplied evidence."
            ),
            context={"evaluation": evaluation},
            max_tokens=420,
        )

