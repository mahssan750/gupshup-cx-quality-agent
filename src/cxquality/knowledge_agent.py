"""AI-assisted Knowledge drafting with deterministic duplicate and conflict context."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .ai import OpenRouterClient, OpenRouterError
from .db import Database

KINDS = {"new", "duplicate", "conflict", "update", "insufficient"}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"\[rule\s+k\d+\]", " ", value)
    value = re.sub("[أإآ]", "ا", value)
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _tokens(value: str) -> set[str]:
    return {word for word in _normalized(value).split() if len(word) > 1}


def _similarity(left: str, right: str) -> float:
    left_normalized, right_normalized = _normalized(left), _normalized(right)
    if not left_normalized or not right_normalized:
        return 0.0
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    return round(0.65 * jaccard + 0.35 * sequence, 4)


def _shortlist(candidate: dict[str, Any], knowledge: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = " ".join(
        [
            str(candidate.get("title") or ""),
            str(candidate.get("rationale") or ""),
            " ".join(str(value) for value in candidate.get("evidence") or []),
        ]
    )
    ranked = []
    for item in knowledge:
        text = f"{item['title']} {item['content']}"
        ranked.append(
            {
                "id": int(item["id"]),
                "title": str(item["title"]),
                "content": str(item["content"]),
                "category": str(item["category"]),
                "similarity": _similarity(query, text),
            }
        )
    ranked.sort(key=lambda item: float(item["similarity"]), reverse=True)
    return ranked[:8]


def analyze_run_candidates(
    db: Database,
    run_id: str,
    client: OpenRouterClient,
) -> dict[str, Any]:
    """Draft and classify pending rules while preserving supervisor activation control."""

    candidates = [
        item
        for item in db.list_knowledge_candidates("pending", limit=500)
        if str(item["run_id"]) == run_id and item.get("analysis_status") != "complete"
    ]
    knowledge = db.list_knowledge(include_inactive=False)
    stats: dict[str, Any] = {
        "analyzed": 0,
        "failed": 0,
        "cost_usd": 0.0,
        "kinds": {},
    }
    valid_knowledge_ids = {int(item["id"]) for item in knowledge}
    for candidate in candidates:
        comparisons = _shortlist(candidate, knowledge)
        try:
            task = (
                    "Act as this company's senior customer-service Knowledge editor. Based only "
                    "on the recurring gap, representative conversation evidence, and supplied "
                    "existing rules: propose one concise reusable Arabic company rule; decide "
                    "whether it is new, duplicate, conflict, update, or insufficient; and identify "
                    "the closest supplied rule ID. Return exactly: action, proposed_rule, "
                    "similar_knowledge_id, reason, conflict_notes, confidence. Never turn a "
                    "customer or employee name, phone number, penalty amount, or one-off fact into "
                    "a rule. A conflict means the proposed behavior and an existing rule prescribe "
                    "different actions under the same visible conditions. Use insufficient when "
                    "the evidence cannot support a reusable rule."
                )
            context = {
                    "gap_title": candidate["title"],
                    "gap_rationale": candidate["rationale"],
                    "occurrence_count": candidate["occurrence_count"],
                    "representative_evidence": candidate.get("evidence") or [],
                    "closest_existing_rules": comparisons[:5],
                    "allowed_actions": sorted(KINDS),
                }
            try:
                result = client.write_json(task=task, context=context, max_tokens=420)
            except OpenRouterError:
                result = client.write_json(
                    task=(
                        task
                        + " The previous provider response was invalid or incomplete. Return the "
                        "six requested keys in one short JSON object with no markdown."
                    ),
                    context=context,
                    max_tokens=650,
                )
            kind = str(result.get("action") or "insufficient").strip().casefold()
            if kind not in KINDS:
                kind = "insufficient"
            proposed_rule = str(result.get("proposed_rule") or "").strip()
            try:
                similar_id = int(result["similar_knowledge_id"])
            except (KeyError, TypeError, ValueError):
                similar_id = None
            if similar_id not in valid_knowledge_ids:
                similar_id = int(comparisons[0]["id"]) if comparisons and kind != "new" else None
            top_similarity = float(comparisons[0]["similarity"]) if comparisons else 0.0
            if proposed_rule and comparisons:
                proposed_similarity = _similarity(proposed_rule, comparisons[0]["content"])
                top_similarity = max(top_similarity, proposed_similarity)
                if proposed_similarity >= 0.92:
                    kind = "duplicate"
                    similar_id = int(comparisons[0]["id"])
            db.update_knowledge_candidate_analysis(
                int(candidate["id"]),
                analysis_status="complete",
                candidate_kind=kind,
                proposed_rule=proposed_rule,
                similar_knowledge_id=similar_id,
                similarity_score=top_similarity,
                comparisons=comparisons,
                ai_reason=str(result.get("reason") or ""),
                conflict_notes=str(result.get("conflict_notes") or ""),
                ai_model=client.config.writer_model,
            )
            stats["analyzed"] += 1
            stats["kinds"][kind] = int(stats["kinds"].get(kind, 0)) + 1
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            db.update_knowledge_candidate_analysis(
                int(candidate["id"]),
                analysis_status="failed",
                candidate_kind="unreviewed",
                comparisons=comparisons,
                ai_reason=f"{type(exc).__name__}: AI analysis was not completed",
                ai_model=client.config.writer_model,
            )
            stats["failed"] += 1
        finally:
            cost = client.consume_thread_cost()
            if cost:
                db.record_spend(
                    {
                        "run_id": run_id,
                        "provider": "OpenRouter",
                        "model": client.config.writer_model,
                        "request_type": "knowledge-candidate-analysis",
                        "cost_usd": cost,
                    }
                )
                stats["cost_usd"] += cost
    stats["cost_usd"] = round(float(stats["cost_usd"]), 9)
    return stats
