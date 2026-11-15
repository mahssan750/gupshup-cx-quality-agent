"""Transparent QA review rules shared by reporting and manager overrides."""

from __future__ import annotations

from collections.abc import Mapping

COMPONENTS = {
    "company_compliance": {
        "label": "Company rule compliance",
        "weight": 1.0,
        "mistake_threshold": 2.5,
        "meaning": "The agent response conflicts with an approved company rule or supervisor precedent.",
    },
}


def score_band(value: float) -> str:
    if value < 1.5:
        return "serious concern"
    if value < 2.5:
        return "needs improvement"
    if value < 3.5:
        return "acceptable"
    return "strong"


def explain_component(component: str, value: float) -> str:
    meta = COMPONENTS[component]
    return (
        f"AI classified this factor as {score_band(value)}. {meta['meaning']}"
    )


def mistake_flags(scores: Mapping[str, object]) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    for component, meta in COMPONENTS.items():
        try:
            value = float(scores[component])
        except (KeyError, TypeError, ValueError):
            continue
        threshold = float(meta["mistake_threshold"])
        if value < threshold:
            flags.append(
                {
                    "component": component,
                    "label": meta["label"],
                    "value": value,
                    "threshold": threshold,
                    "explanation": explain_component(component, value),
                }
            )
    return flags


def normalized_component_scores(values: Mapping[str, object]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for component in COMPONENTS:
        try:
            value = float(values[component])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"A numeric {component.replace('_', ' ')} score is required") from exc
        if value < 0 or value > 4:
            raise ValueError(f"{component.replace('_', ' ').title()} must be between 0 and 4")
        scores[component] = round(value, 3)
    scores["overall"] = recompute_overall(scores)
    scores["_rubric_version"] = 3
    return scores


def recompute_overall(scores: Mapping[str, float]) -> float:
    return round(float(scores["company_compliance"]) / 4.0 * 100, 2)
