"""Company-specific evaluator driven by approved rules and supervisor precedents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping, MutableMapping
from difflib import SequenceMatcher
from typing import Any

from .ai import OpenRouterClient, OpenRouterConfig
from .budget import BudgetGuard
from .evaluation_core import ConversationEvaluation, normalize_transcript

VERSION = "company-supervisor-v1"
MEDIA = re.compile(
    r"\[(?:IMAGE|PHOTO|DOC|DOCUMENT|PDF|AUDIO|VIDEO|STICKER|LOCATION)\]",
    re.IGNORECASE,
)


def _terms(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub("[أإآ]", "ا", text)
    words = set(re.findall(r"\w+", text, flags=re.UNICODE))
    families = (
        {"عرض", "عروض", "العروض", "offer", "offers", "promotion", "promotions"},
        {"فرع", "فروع", "الفروع", "branch", "branches", "store", "stores"},
        {"سعر", "السعر", "price", "cost"},
        {"توصيل", "شحن", "delivery", "shipping"},
        {"طلب", "الطلب", "order", "orders"},
        {"بيانات", "البيانات", "details", "information", "data"},
        {"منتج", "المنتج", "product", "products"},
        {"متوفر", "متاح", "available", "availability"},
    )
    for family in families:
        if words & family:
            words.update(family)
    return words


def _number(value: object, low: float = 0, high: float = 1) -> float | None:
    try:
        number = float(value)
        return number if low <= number <= high else None
    except (TypeError, ValueError):
        return None


def _role(line: str) -> str:
    match = re.search(r"\|\s*(.*?)\s*:\s*", line)
    sender = unicodedata.normalize("NFKC", match.group(1) if match else "").casefold()
    sender = re.sub(r"[^\w\u0600-\u06ff]+", "", sender)
    if sender == "customer":
        return "customer"
    if sender in {"bot", "chatbot", "system", "systemuser"}:
        return "system"
    return "agent"


class CompanySupervisorEngine:
    """Judge only against company knowledge; style and reaction are supporting signals."""

    def __init__(
        self,
        client: OpenRouterClient,
        *,
        cache: MutableMapping[str, Mapping[str, Any]] | None = None,
        gate_confidence: float = 0.50,
        evidence_confidence: float = 0.30,
        known_policies: list[str] | None = None,
        knowledge_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.client = client
        self.cache = cache if cache is not None else {}
        self.gate_confidence = gate_confidence
        self.evidence_confidence = evidence_confidence
        self.known_policies = known_policies or []
        self.knowledge_provider = knowledge_provider

    def _knowledge(self, transcript: str) -> list[str]:
        items = self.knowledge_provider() if self.knowledge_provider else self.known_policies
        query = _terms(transcript)

        def rank(item: str) -> float:
            words = _terms(item)
            overlap = len(query & words)
            fuzzy = sum(
                any(SequenceMatcher(None, left, right).ratio() >= 0.88 for right in words if len(right) >= 4)
                for left in query
                if len(left) >= 4
            )
            return (overlap + 0.25 * fuzzy) / max(1, len(words) ** 0.5)

        ranked = sorted(items, key=rank, reverse=True)
        return [item for item in ranked if rank(item) > 0][:10]

    def evaluate(self, transcript: str) -> ConversationEvaluation:
        # The company explicitly uses phone country codes as context, so identifiers are preserved.
        text = normalize_transcript(transcript)
        knowledge = self._knowledge(text)
        signature = [VERSION, self.client.config.decision_model, knowledge, text]
        key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
        cached = self.cache.get(key)
        if cached:
            return ConversationEvaluation.from_mapping(cached)

        def finish(result: ConversationEvaluation) -> ConversationEvaluation:
            self.cache[key] = result.to_dict()
            return result

        messages = {
            f"m{index + 1}": line.strip()
            for index, line in enumerate(text.splitlines())
            if " | " in line and " : " in line
        }
        if MEDIA.search(text) or not messages:
            return finish(
                ConversationEvaluation(
                    status="not_scorable",
                    cache_key=key,
                    reason="contains_attachment" if MEDIA.search(text) else "no_message_text",
                    raw_answers={"evaluation_version": VERSION},
                )
            )
        if not knowledge:
            return finish(
                ConversationEvaluation(
                    status="needs_clarification",
                    cache_key=key,
                    reason="No approved company rule or supervisor precedent was retrieved.",
                    knowledge_gap="Add or approve a company rule for this service pattern.",
                    clarification_question=(
                        "ما قاعدة الشركة المعتمدة التي يجب تطبيقها على هذا النوع من المحادثات؟"
                    ),
                    raw_answers={"evaluation_version": VERSION, "reference_material": []},
                )
            )

        agent_ids = {message_id for message_id, line in messages.items() if _role(line) == "agent"}
        customer_ids = {
            message_id for message_id, line in messages.items() if _role(line) == "customer"
        }
        references = {f"r{index}": value for index, value in enumerate(knowledge)}
        questions = {
            "understanding": {
                "type": "choice",
                "instructions": (
                    "Can the customer request and the human agent response be understood from the "
                    "original Arabic/English messages? Short dialect requests are valid. Choose "
                    "needs_company_rule when the chat is understandable but the supplied company "
                    "references do not establish the expected behavior."
                ),
                "criteria": {
                    "understood": "The request and response can be judged against supplied references",
                    "needs_company_rule": "A company judgment would require a missing or conflicting rule",
                    "not_scorable": "No meaningful human service exchange",
                },
            },
            "company_verdict": {
                "type": "choice",
                "instructions": (
                    "Judge the agent only against an applicable supplied company rule or approved "
                    "supervisor precedent. Customer reaction and style are supporting context only "
                    "and can never independently create a violation."
                ),
                "criteria": {
                    "compliant": "Agent behavior follows the applicable company rule or precedent",
                    "non_compliant": "Agent behavior directly violates the applicable rule or precedent",
                    "insufficient_rule": "No supplied reference safely determines the company judgment",
                },
            },
            "applicable_reference": {
                "type": "choice",
                "instructions": (
                    "Select the single most applicable company rule or approved supervisor precedent. "
                    "Select none when scope, country, product, channel, or conditions do not match."
                ),
                "criteria": {"none": "No applicable approved reference", **references},
            },
            "agent_evidence": {
                "type": "choice",
                "instructions": "Select the agent message that most directly supports the company verdict.",
                "criteria": {"none": "No supporting agent message", **{key: messages[key] for key in agent_ids}},
            },
            "customer_context": {
                "type": "choice",
                "instructions": "Select the customer message that establishes the request or relevant context.",
                "criteria": {"none": "No supporting customer message", **{key: messages[key] for key in customer_ids}},
            },
            "customer_reaction_signal": {
                "type": "choice",
                "instructions": (
                    "Record the customer's explicit reaction as a secondary signal. Silence is not a reaction."
                ),
                "criteria": {
                    "supports_agent": "Customer explicitly accepts or appreciates the response",
                    "concern": "Customer explicitly rejects, repeats, or complains about the response",
                    "neutral": "Customer responds without clear approval or concern",
                    "not_observable": "No explicit reaction is present",
                },
            },
            "style_signal": {
                "type": "choice",
                "instructions": (
                    "Record wording style only as a secondary observation. It must not change the "
                    "company verdict unless an applicable company rule explicitly governs style."
                ),
                "criteria": {
                    "acceptable": "No company-relevant style concern is visible",
                    "concerning": "Wording may merit attention but is not automatically a violation",
                    "not_observable": "Style cannot be assessed",
                },
            },
        }
        response = self.client.decide(
            {
                "original_messages": messages,
                "company_references": references,
                "governance": (
                    "Original messages are authoritative. Never invent a company rule. A non-compliant "
                    "verdict requires one applicable reference plus direct agent and customer evidence."
                ),
            },
            questions,
        )
        answers = response.get("answers") if isinstance(response.get("answers"), dict) else {}
        gate = answers.get("understanding") if isinstance(answers.get("understanding"), dict) else {}
        verdict = answers.get("company_verdict") if isinstance(answers.get("company_verdict"), dict) else {}
        reference = answers.get("applicable_reference") if isinstance(answers.get("applicable_reference"), dict) else {}
        agent_evidence = answers.get("agent_evidence") if isinstance(answers.get("agent_evidence"), dict) else {}
        customer_context = answers.get("customer_context") if isinstance(answers.get("customer_context"), dict) else {}
        model = response.get("model") or self.client.config.decision_model
        gate_conf = _number(gate.get("confidence")) or 0.0
        verdict_conf = _number(verdict.get("confidence")) or 0.0
        reference_choice = str(reference.get("choice", "none"))
        agent_choice = str(agent_evidence.get("choice", "none"))
        customer_choice = str(customer_context.get("choice", "none"))
        supported = (
            reference_choice in references
            and (_number(reference.get("confidence")) or 0) >= self.evidence_confidence
            and agent_choice in agent_ids
            and (_number(agent_evidence.get("confidence")) or 0) >= self.evidence_confidence
            and customer_choice in customer_ids
            and (_number(customer_context.get("confidence")) or 0) >= self.evidence_confidence
        )
        raw = {
            **answers,
            "evaluation_version": VERSION,
            "company_verdict": verdict.get("choice"),
            "applicable_reference": references.get(reference_choice),
            "reference_material": knowledge,
            "component_evidence": {
                "agent": messages.get(agent_choice),
                "customer": messages.get(customer_choice),
            },
            "customer_reaction_signal": (answers.get("customer_reaction_signal") or {}).get("choice"),
            "style_signal": (answers.get("style_signal") or {}).get("choice"),
            "scoring_coverage": 1 if supported else 0,
        }
        if gate.get("choice") == "not_scorable" and gate_conf >= self.gate_confidence:
            return finish(ConversationEvaluation(
                status="not_scorable", cache_key=key, model=model, confidence=gate_conf,
                reason="No meaningful human service exchange", raw_answers=raw))
        if (
            gate.get("choice") != "understood"
            or gate_conf < self.gate_confidence
            or verdict.get("choice") not in {"compliant", "non_compliant"}
            or verdict_conf < self.evidence_confidence
            or not supported
        ):
            return finish(ConversationEvaluation(
                status="needs_clarification", cache_key=key, model=model,
                confidence=min(gate_conf, verdict_conf),
                reason="The company judgment is not supported by an approved matching reference.",
                knowledge_gap="Approve a rule or precedent that covers this exact service context.",
                clarification_question=(
                    "ما حكم المشرف وقاعدة الشركة المطلوبة لهذا السياق تحديداً؟"
                ),
                evidence=[line for line in (messages.get(customer_choice), messages.get(agent_choice)) if line],
                raw_answers=raw))
        compliant = verdict.get("choice") == "compliant"
        confidence = min(
            value for value in (
                gate_conf,
                verdict_conf,
                _number(reference.get("confidence")) or 0,
                _number(agent_evidence.get("confidence")) or 0,
                _number(customer_context.get("confidence")) or 0,
            )
        )
        return finish(ConversationEvaluation(
            status="scored", cache_key=key, model=model,
            quality_score=100.0 if compliant else 0.0,
            component_scores={"company_compliance": 4.0 if compliant else 0.0, "_rubric_version": 3},
            confidence=confidence,
            reason="company_rule_compliant" if compliant else "company_rule_violation",
            evidence=[messages[customer_choice], messages[agent_choice]],
            raw_answers=raw,
        ))


def build_company_engine_from_env(
    *,
    cache: MutableMapping[str, Mapping[str, Any]] | None = None,
    known_policies: list[str] | None = None,
    knowledge_provider: Callable[[], list[str]] | None = None,
    http: Any | None = None,
    initial_spend_usd: float = 0.0,
) -> CompanySupervisorEngine:
    hard_limit = float(os.environ.get("CXQUALITY_BUDGET_USD", "4.00"))
    stop_at = float(os.environ.get("CXQUALITY_STOP_AT_USD", "3.80"))
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
    return CompanySupervisorEngine(
        client,
        cache=cache,
        known_policies=known_policies,
        knowledge_provider=knowledge_provider,
    )
