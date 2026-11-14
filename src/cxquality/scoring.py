"""Evidence-backed conversation scoring using typed Jev decisions."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .ai import OpenRouterClient, OpenRouterConfig, OpenRouterError
from .budget import BudgetGuard

STATUS_SCORED = "scored"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_NOT_SCORABLE = "not_scorable"


@dataclass
class ConversationEvaluation:
    status: str
    cache_key: str
    model: str | None = None
    quality_score: float | None = None
    component_scores: dict[str, float] = field(default_factory=dict)
    sentiment_start: str | None = None
    sentiment_end: str | None = None
    tone: str | None = None
    satisfaction_score: float | None = None
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


def transcript_hash(text: str) -> str:
    return hashlib.sha256(normalize_transcript(text).encode("utf-8")).hexdigest()


def redact_transcript_for_model(text: str) -> str:
    """Remove direct identifiers while preserving words and tone needed for judgment."""
    value = normalize_transcript(text)
    match = re.search(
        r"Customer Name:\s*(.+?)(?=\s+Chat Started At:|\r?\n)",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        customer_name = match.group(1).strip()
        value = value[: match.start(1)] + "[customer]" + value[match.end(1) :]
        if customer_name:
            value = re.sub(re.escape(customer_name), "Customer", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", value)
    value = re.sub(r"(?<!\d)\+?\d[\d\s().-]{7,}\d(?!\d)", "[number]", value)
    value = re.sub(r"(?<!\d)\d{7,}(?!\d)", "[identifier]", value)
    return value


def scoring_questions() -> dict[str, Any]:
    """Frozen, explicit rubric. Question ids are not relied on as instructions."""
    five_quality_levels = [
        "Very poor: actively unhelpful, dismissive, confusing, or harmful",
        "Poor: major problems and customer need remains largely unmet",
        "Acceptable: partially helpful but important gaps remain",
        "Good: helpful, clear, and substantially meets the customer need",
        "Excellent: complete, efficient, empathetic, and easy for the customer",
    ]
    return {
        "understanding": {
            "type": "choice",
            "instructions": (
                "Is there enough meaningful customer and human-agent dialogue to judge service "
                "quality from the supplied text? Score observable interaction quality even when "
                "business context is incomplete. A missing agent answer, excessive delay, generic "
                "or irrelevant reply, unresolved request, repeated complaint, or explicit customer "
                "dissatisfaction is evidence for a low score, not a reason for clarification. "
                "Choose clarification only when the language or customer goal is genuinely "
                "unintelligible, or when an essential product/policy fact makes opposite judgments "
                "equally plausible."
                " Apply manager-approved knowledge supplied with the conversation whenever it is "
                "relevant, and do not request clarification for an already answered pattern."
            ),
            "criteria": {
                "understood": "Meaningful exchange with enough evidence for a reliable score",
                "needs_clarification": (
                    "Meaningful exchange exists, but essential ambiguity could reverse the judgment"
                ),
                "not_scorable": (
                    "Bot-only, spam, accidental contact, opt-out, empty/media-only, or no meaningful "
                    "human service exchange"
                ),
            },
        },
        "sentiment_start": {
            "type": "choice",
            "instructions": "Customer sentiment at the beginning, based on the customer's words.",
            "criteria": {
                "positive": "Friendly, thankful, enthusiastic, or clearly pleased",
                "neutral": "Factual request with no strong positive or negative tone",
                "negative": "Frustrated, angry, disappointed, worried, or complaining",
                "unknown": "Insufficient customer language to infer sentiment",
            },
        },
        "sentiment_end": {
            "type": "choice",
            "instructions": "Customer sentiment at the end, based on the customer's final responses.",
            "criteria": {
                "positive": "Satisfied, thankful, reassured, or accepts the resolution",
                "neutral": "No strong satisfaction or dissatisfaction is expressed",
                "negative": "Still frustrated, rejects the response, repeats issue, or complains",
                "unknown": "No customer response or insufficient evidence after the agent answer",
            },
        },
        "customer_satisfaction": {
            "type": "score",
            "instructions": (
                "Estimate expressed or behaviorally supported customer satisfaction. Focus on the "
                "customer's words, tone, acceptance, repetition, and final response; do not assume "
                "silence means satisfaction."
            ),
            "criteria": [
                "Very dissatisfied or openly rejects the service",
                "Dissatisfied; frustration or unmet need remains",
                "Unclear or mixed satisfaction",
                "Satisfied; accepts the answer or responds positively",
                "Highly satisfied; clear gratitude, relief, or enthusiastic acceptance",
            ],
        },
        "resolution": {
            "type": "score",
            "instructions": "How completely and efficiently did the agent address the customer's need?",
            "criteria": five_quality_levels,
        },
        "empathy": {
            "type": "score",
            "instructions": "How empathetic, respectful, and attentive was the agent's behavior?",
            "criteria": five_quality_levels,
        },
        "clarity": {
            "type": "score",
            "instructions": "How clear, relevant, and easy to act on were the agent's responses?",
            "criteria": five_quality_levels,
        },
        "tone": {
            "type": "choice",
            "instructions": "Overall effect of the human agent's tone on the customer experience.",
            "criteria": {
                "supportive": "Warm, patient, respectful, and helpful",
                "professional": "Clear and appropriate, though not especially warm",
                "neutral": "Functional with little emotional effect",
                "abrupt": "Cold, rushed, dismissive, repetitive, or overly scripted",
                "harmful": "Hostile, insulting, misleading, or seriously inappropriate",
            },
        },
    }


def _choice(answer: Mapping[str, Any], default: str = "unknown") -> tuple[str, float]:
    choice = str(answer.get("choice", default))
    confidence = float(answer.get("confidence", 0.0) or 0.0)
    return choice, confidence


def _score(answer: Mapping[str, Any]) -> tuple[float | None, float]:
    try:
        return float(answer["score"]), float(answer.get("confidence", 0.0) or 0.0)
    except (KeyError, TypeError, ValueError):
        return None, 0.0


def _evidence_lines(text: str, limit: int = 5) -> list[str]:
    """Select compact exact message lines without logging or inventing content."""
    lines = []
    for line in normalize_transcript(text).splitlines():
        line = line.strip()
        if " | " not in line or " : " not in line:
            continue
        line = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", line)
        line = re.sub(r"(?<!\d)\+?\d[\d\s().-]{7,}\d(?!\d)", "[number]", line)
        lines.append(line[:500])
    if len(lines) <= limit:
        return lines
    # Beginning, middle interaction, and ending are more useful than five adjacent messages.
    indexes = sorted({0, 1, len(lines) // 2, len(lines) - 2, len(lines) - 1})
    return [lines[index] for index in indexes[:limit]]


class ScoringEngine:
    def __init__(
        self,
        client: OpenRouterClient,
        *,
        cache: MutableMapping[str, Mapping[str, Any]] | None = None,
        gate_confidence: float = 0.60,
        component_confidence: float = 0.45,
        known_policies: list[str] | None = None,
        knowledge_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.client = client
        self.cache = cache if cache is not None else {}
        self.gate_confidence = gate_confidence
        self.component_confidence = component_confidence
        self.known_policies = known_policies or []
        self.knowledge_provider = knowledge_provider

    def _knowledge(self, transcript: str) -> list[str]:
        items = self.knowledge_provider() if self.knowledge_provider else self.known_policies
        transcript_terms = set(re.findall(r"\w+", transcript.casefold(), flags=re.UNICODE))
        ranked: list[tuple[float, str]] = []
        for item in items:
            item_terms = set(re.findall(r"\w+", item.casefold(), flags=re.UNICODE))
            overlap = len(transcript_terms & item_terms)
            if overlap:
                ranked.append((overlap / max(1, len(item_terms) ** 0.5), item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:8]]

    def evaluate(self, transcript: str) -> ConversationEvaluation:
        normalized = normalize_transcript(transcript)
        model_text = redact_transcript_for_model(normalized)
        key = transcript_hash(normalized)
        cached = self.cache.get(key)
        if cached:
            return ConversationEvaluation.from_mapping(cached)

        if len(normalized) < 80 or " | " not in normalized:
            result = ConversationEvaluation(
                status=STATUS_NOT_SCORABLE,
                cache_key=key,
                reason="No meaningful timestamped conversation was available.",
            )
            self.cache[key] = result.to_dict()
            return result

        relevant_knowledge = self._knowledge(model_text)
        response = self.client.decide(
            {
                "conversation": model_text,
                "manager_approved_knowledge": relevant_knowledge,
            },
            scoring_questions(),
        )
        answers = response.get("answers") or {}
        gate, gate_conf = _choice(answers.get("understanding") or {})
        model = response.get("model") or self.client.config.decision_model

        if gate == "not_scorable":
            result = ConversationEvaluation(
                status=STATUS_NOT_SCORABLE,
                cache_key=key,
                model=model,
                confidence=gate_conf,
                reason="The exchange does not contain a meaningful human service interaction.",
                evidence=_evidence_lines(model_text),
                raw_answers=dict(answers),
            )
            self.cache[key] = result.to_dict()
            return result

        score_names = ("customer_satisfaction", "resolution", "empathy", "clarity")
        parsed_scores = {name: _score(answers.get(name) or {}) for name in score_names}
        weak = [name for name, (score, conf) in parsed_scores.items() if score is None or conf < self.component_confidence]
        needs_clarification = gate != "understood" or gate_conf < self.gate_confidence or bool(weak)
        if needs_clarification:
            uncertainty = (
                "Jev marked the conversation as requiring clarification."
                if gate != "understood"
                else "Low-confidence fields: " + ", ".join(weak or ["understanding"])
            )
            clarification = self._make_clarification(
                model_text, uncertainty, relevant_knowledge=relevant_knowledge
            )
            result = ConversationEvaluation(
                status=STATUS_NEEDS_CLARIFICATION,
                cache_key=key,
                model=model,
                confidence=gate_conf,
                reason=uncertainty,
                clarification_question=clarification.get("question"),
                knowledge_gap=clarification.get("knowledge_gap"),
                evidence=_evidence_lines(model_text),
                raw_answers=dict(answers),
            )
            self.cache[key] = result.to_dict()
            return result

        sentiment_start, sentiment_start_conf = _choice(answers.get("sentiment_start") or {})
        sentiment_end, sentiment_end_conf = _choice(answers.get("sentiment_end") or {})
        tone, tone_conf = _choice(answers.get("tone") or {})
        satisfaction = parsed_scores["customer_satisfaction"][0]
        tone_value = {
            "harmful": 0.0,
            "abrupt": 1.0,
            "neutral": 2.0,
            "professional": 3.0,
            "supportive": 4.0,
        }.get(tone, 2.0)
        component_values = {
            "customer_satisfaction": satisfaction or 0.0,
            "resolution": parsed_scores["resolution"][0] or 0.0,
            "empathy": parsed_scores["empathy"][0] or 0.0,
            "clarity": parsed_scores["clarity"][0] or 0.0,
            "tone": tone_value,
        }
        weights = {
            "customer_satisfaction": 0.30,
            "resolution": 0.25,
            "empathy": 0.15,
            "clarity": 0.15,
            "tone": 0.15,
        }
        quality = sum((component_values[name] / 4.0) * 100 * weight for name, weight in weights.items())
        confidences = [
            gate_conf,
            sentiment_start_conf,
            sentiment_end_conf,
            tone_conf,
            *(confidence for _, confidence in parsed_scores.values()),
        ]
        result = ConversationEvaluation(
            status=STATUS_SCORED,
            cache_key=key,
            model=model,
            quality_score=round(quality, 2),
            component_scores={name: round(value, 3) for name, value in component_values.items()},
            sentiment_start=sentiment_start,
            sentiment_end=sentiment_end,
            tone=tone,
            satisfaction_score=satisfaction,
            confidence=round(sum(confidences) / len(confidences), 4),
            evidence=_evidence_lines(model_text),
            raw_answers=dict(answers),
        )
        self.cache[key] = result.to_dict()
        return result

    def _make_clarification(
        self, transcript: str, uncertainty: str, *, relevant_knowledge: list[str] | None = None
    ) -> dict[str, Any]:
        excerpt = "\n".join(_evidence_lines(transcript, limit=8))
        try:
            return self.client.clarification_question(
                transcript_excerpt=excerpt,
                uncertainty=uncertainty,
                known_policies=relevant_knowledge or [],
            )
        except OpenRouterError:
            # The conversation remains unscored; a writer failure must never turn it into a score.
            return {
                "question": "What missing business context should be used to evaluate this conversation?",
                "knowledge_gap": uncertainty,
                "suggested_action": "answer",
            }


def coverage_metrics(evaluations: list[ConversationEvaluation]) -> dict[str, float | int]:
    total = len(evaluations)
    scored = sum(item.status == STATUS_SCORED for item in evaluations)
    clarification = sum(item.status == STATUS_NEEDS_CLARIFICATION for item in evaluations)
    excluded = sum(item.status == STATUS_NOT_SCORABLE for item in evaluations)
    understood_population = scored + clarification
    return {
        "total": total,
        "scored": scored,
        "needs_clarification": clarification,
        "not_scorable": excluded,
        "scoring_coverage": scored / understood_population if understood_population else 0.0,
        "clarification_rate": clarification / understood_population if understood_population else 0.0,
        "exclusion_rate": excluded / total if total else 0.0,
    }


def build_engine_from_env(
    *,
    cache: MutableMapping[str, Mapping[str, Any]] | None = None,
    known_policies: list[str] | None = None,
    knowledge_provider: Callable[[], list[str]] | None = None,
    http: Any | None = None,
    hard_limit_usd: float | None = None,
    stop_at_usd: float | None = None,
    initial_spend_usd: float = 0.0,
) -> ScoringEngine:
    """Create the default production scorer without exposing secrets to callers or logs."""
    hard_limit = (
        float(hard_limit_usd)
        if hard_limit_usd is not None
        else float(os.environ.get("CXQUALITY_BUDGET_USD", "4.00"))
    )
    stop_at = (
        float(stop_at_usd)
        if stop_at_usd is not None
        else float(os.environ.get("CXQUALITY_STOP_AT_USD", "3.80"))
    )
    client = OpenRouterClient(
        budget=BudgetGuard(
            hard_limit_usd=hard_limit,
            stop_at_usd=stop_at,
            initial_spend_usd=initial_spend_usd,
        ),
        config=OpenRouterConfig(
            decision_model=os.environ.get("CXQUALITY_DECISION_MODEL", "typesafe/jev-1.14"),
            writer_model=os.environ.get("CXQUALITY_WRITER_MODEL", "openai/gpt-6-luna"),
        ),
        http=http,
    )
    from .evaluation import EvidenceScoringEngine

    return EvidenceScoringEngine(
        client,
        cache=cache,
        known_policies=known_policies,
        knowledge_provider=knowledge_provider,
    )

