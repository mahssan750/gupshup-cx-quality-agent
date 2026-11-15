from __future__ import annotations

from types import SimpleNamespace

import pytest

from cxquality.ai import OpenRouterError
from cxquality.db import Database
from cxquality.knowledge_agent import analyze_run_candidates


class FakeWriter:
    def __init__(self, response: dict[str, object]):
        self.response = response
        self.config = SimpleNamespace(writer_model="test/knowledge-writer")

    def write_json(self, **_: object) -> dict[str, object]:
        return dict(self.response)

    def consume_thread_cost(self) -> float:
        return 0.0


class RetryWriter(FakeWriter):
    def __init__(self, response: dict[str, object]):
        super().__init__(response)
        self.calls = 0

    def write_json(self, **_: object) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            raise OpenRouterError("invalid first response")
        return dict(self.response)


def _candidate(db: Database, run_id: str, session_id: str = "gap") -> int:
    db.upsert_conversation(
        {
            "session_id": session_id,
            "run_id": run_id,
            "source_zip": f"{session_id}.zip",
            "transcript_hash": f"hash-{session_id}",
            "eligible": True,
        }
    )
    db.upsert_evaluation(
        {"run_id": run_id, "session_id": session_id, "status": "needs_clarification"}
    )
    db.queue_clarification(
        run_id=run_id,
        session_id=session_id,
        question="What rule applies to the product availability request?",
        context_summary="The customer asks if the product is available.",
    )
    db.sync_knowledge_candidates(run_id)
    return int(db.list_knowledge_candidates("pending")[0]["id"])


def test_ai_duplicate_detection_blocks_new_knowledge_copy(tmp_path) -> None:
    db = Database(tmp_path / "duplicate.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    existing_id = db.add_knowledge(
        "Availability",
        "عند سؤال العميل عن توفر المنتج، يجب توضيح التوفر والبديل المتاح.",
        category="company-rule",
    )
    candidate_id = _candidate(db, run_id)
    result = analyze_run_candidates(
        db,
        run_id,
        FakeWriter(
            {
                "action": "new",
                "proposed_rule": (
                    "عند سؤال العميل عن توفر المنتج، يجب توضيح التوفر والبديل المتاح."
                ),
                "similar_knowledge_id": existing_id,
                "reason": "The same behavior already exists.",
                "conflict_notes": "",
                "confidence": 0.98,
            }
        ),
    )
    assert result["kinds"] == {"duplicate": 1}
    candidate = db.list_knowledge_candidates("pending")[0]
    assert candidate["candidate_kind"] == "duplicate"
    assert candidate["similar_knowledge_id"] == existing_id
    with pytest.raises(ValueError, match="matches an existing rule"):
        db.review_knowledge_candidate(
            candidate_id,
            decision="approved",
            resolution="new",
            rule_text=candidate["proposed_rule"],
        )
    db.review_knowledge_candidate(
        candidate_id,
        decision="rejected",
        resolution="duplicate",
    )
    assert len(db.list_knowledge(include_inactive=False)) == 1


def test_ai_update_resolution_changes_existing_rule_without_increasing_count(tmp_path) -> None:
    db = Database(tmp_path / "update.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    existing_id = db.add_knowledge(
        "Availability",
        "Tell the customer whether the product is available.",
        category="company-rule",
    )
    candidate_id = _candidate(db, run_id)
    analyze_run_candidates(
        db,
        run_id,
        FakeWriter(
            {
                "action": "update",
                "proposed_rule": (
                    "عند عدم توفر المنتج، وضّح عدم التوفر وأرسل بديلًا مناسبًا للعميل."
                ),
                "similar_knowledge_id": existing_id,
                "reason": "The existing rule misses the approved alternative step.",
                "conflict_notes": "No direct conflict; this extends the existing rule.",
                "confidence": 0.86,
            }
        ),
    )
    candidate = db.list_knowledge_candidates("pending")[0]
    assert candidate["candidate_kind"] == "update"
    resolved_id = db.review_knowledge_candidate(
        candidate_id,
        decision="approved",
        resolution="update",
        rule_text=candidate["proposed_rule"],
    )
    assert resolved_id == existing_id
    knowledge = db.list_knowledge(include_inactive=False)
    assert len(knowledge) == 1
    assert "بديلًا مناسبًا" in knowledge[0]["content"]


def test_ai_candidate_analysis_retries_one_invalid_writer_response(tmp_path) -> None:
    db = Database(tmp_path / "retry.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    _candidate(db, run_id)
    writer = RetryWriter(
        {
            "action": "insufficient",
            "proposed_rule": "",
            "similar_knowledge_id": None,
            "reason": "The evidence is not sufficient for a reusable rule.",
            "conflict_notes": "",
            "confidence": 0.8,
        }
    )
    result = analyze_run_candidates(db, run_id, writer)
    assert writer.calls == 2
    assert result["analyzed"] == 1
    assert db.list_knowledge_candidates("pending")[0]["analysis_status"] == "complete"


def test_ai_conflict_is_exposed_before_supervisor_resolves_it(tmp_path) -> None:
    db = Database(tmp_path / "conflict.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    existing_id = db.add_knowledge(
        "Availability",
        "When unavailable, do not recommend an alternative.",
        category="company-rule",
    )
    _candidate(db, run_id)
    analyze_run_candidates(
        db,
        run_id,
        FakeWriter(
            {
                "action": "conflict",
                "proposed_rule": "When unavailable, always recommend an approved alternative.",
                "similar_knowledge_id": existing_id,
                "reason": "The two rules prescribe opposite actions under the same condition.",
                "conflict_notes": "Resolve whether alternatives are required before activation.",
                "confidence": 0.92,
            }
        ),
    )
    candidate = db.list_knowledge_candidates("pending")[0]
    assert candidate["candidate_kind"] == "conflict"
    assert candidate["similar_knowledge_id"] == existing_id
    assert "opposite" in candidate["ai_reason"]
    assert "before activation" in candidate["conflict_notes"]
    assert db.list_knowledge(include_inactive=False)[0]["content"].startswith("When unavailable")
