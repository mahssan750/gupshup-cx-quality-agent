"""Transparent QA review rules shared by reporting and manager overrides."""

from __future__ import annotations

from collections.abc import Mapping

COMPONENTS = {
    "customer_satisfaction": {
        "label": "Customer satisfaction",
        "weight": 0.30,
        "mistake_threshold": 2.5,
        "meaning": "The customer's words or behaviour indicate dissatisfaction or weak acceptance.",
    },
    "resolution": {
        "label": "Resolution",
        "weight": 0.25,
        "mistake_threshold": 2.5,
        "meaning": "The need may be unresolved, incomplete, delayed, or inefficiently handled.",
    },
    "empathy": {
        "label": "Empathy",
        "weight": 0.15,
        "mistake_threshold": 2.5,
        "meaning": "The response may lack acknowledgement, care, patience, or attentiveness.",
    },
    "clarity": {
        "label": "Clarity",
        "weight": 0.15,
        "mistake_threshold": 2.5,
        "meaning": "The response may be unclear, irrelevant, incomplete, or difficult to act on.",
    },
    "tone": {
        "label": "Tone",
        "weight": 0.15,
        "mistake_threshold": 2.0,
        "meaning": "The agent's tone may be abrupt, cold, dismissive, or harmful.",
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
        f"AI score {value:.2f}/4 ({score_band(value)}). {meta['meaning']} "
        f"This component contributes {float(meta['weight']) * 100:.0f}% of the overall score."
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
    return scores


def recompute_overall(scores: Mapping[str, float]) -> float:
    quality = sum(
        (float(scores[component]) / 4.0) * 100 * float(meta["weight"])
        for component, meta in COMPONENTS.items()
    )
    return round(quality, 2)
