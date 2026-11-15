"""Shared result contract for the company-supervisor evaluation engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ConversationEvaluation:
    status: str
    cache_key: str
    model: str | None = None
    quality_score: float | None = None
    component_scores: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    reason: str | None = None
    clarification_question: str | None = None
    knowledge_gap: str | None = None
    evidence: list[str] = field(default_factory=list)
    raw_answers: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ConversationEvaluation:
        fields = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in fields if key in value})


def normalize_transcript(text: str) -> str:
    text = re.sub(r"(?im)^\s*Exported from Agent Assist on .*?$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
