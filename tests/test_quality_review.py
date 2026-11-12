from cxquality.db import Database
from cxquality.quality_review import mistake_flags, normalized_component_scores


def _scored_chat(db: Database):
    run_id = db.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips"
    )
    db.upsert_agent(
        {"id": "1998", "staff_id": "1998", "canonical_name": "Agent One", "branch": "Mall"}
    )
    db.upsert_conversation(
        {
            "session_id": "qa-1",
            "run_id": run_id,
            "source_zip": "qa-1.zip",
            "transcript_hash": "hash",
            "eligible": True,
            "agent_id": "1998",
            "assigned_agent_alias": "Agent One",
        }
    )
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "qa-1",
            "status": "scored",
            "model": "test-model",
            "scores": {
                "customer_satisfaction": 2,
                "resolution": 1,
                "empathy": 3,
                "clarity": 2,
                "tone": 1,
                "overall": 43.75,
            },
            "evidence": ["Customer : still waiting", "Agent One : check later"],
            "confidence": 0.8,
            "decision": {"sentiment_end": "negative", "tone": "abrupt"},
        }
    )
    return run_id


def test_mistake_flags_explain_low_components():
    scores = normalized_component_scores(
        {
            "customer_satisfaction": 2,
            "resolution": 1,
            "empathy": 3,
            "clarity": 2,
            "tone": 1,
        }
    )
    flags = mistake_flags(scores)
    assert {item["component"] for item in flags} == {
        "customer_satisfaction",
        "resolution",
        "clarity",
        "tone",
    }
    assert all("AI score" in item["explanation"] for item in flags)


def test_quality_override_updates_score_knowledge_and_audit_history(tmp_path):
    db = Database(tmp_path / "quality.sqlite3")
    db.initialize()
    run_id = _scored_chat(db)
    review_id = db.apply_manual_review(
        run_id=run_id,
        session_id="qa-1",
        review_kind="quality",
        component="overall",
        decision="corrected",
        corrected_scores={
            "customer_satisfaction": 3,
            "resolution": 3,
            "empathy": 4,
            "clarity": 3,
            "tone": 3,
        },
        manager_reason="The full chat shows a complete resolution.",
        knowledge_rule="When the customer explicitly accepts the solution, treat it as resolved.",
    )
    item = db.scored_review_item(run_id, "qa-1")
    assert item["scores"]["overall"] == 78.75
    assert item["review_decision"] == "corrected"
    assert item["ai_decision"]["manual_override"]["decision"] == "corrected"
    assert db.manual_review_history(review_id)[0]["manager_reason"].startswith("The full chat")
    learned = [row for row in db.list_knowledge() if row["source"] == "manager-review"]
    assert len(learned) == 1


def test_mistake_override_changes_component_and_preserves_original_ai_value(tmp_path):
    db = Database(tmp_path / "mistake.sqlite3")
    db.initialize()
    run_id = _scored_chat(db)
    db.apply_manual_review(
        run_id=run_id,
        session_id="qa-1",
        review_kind="mistake",
        component="resolution",
        decision="dismissed",
        corrected_value=3.5,
        manager_reason="The solution was supplied in the complete transcript.",
        knowledge_rule="Count a supplied and accepted next step as a resolution.",
    )
    item = db.scored_review_item(run_id, "qa-1")
    review = db.manual_review_map("mistake")[(run_id, "qa-1", "resolution")]
    assert item["scores"]["resolution"] == 3.5
    assert review["ai_value"] == "1"
    assert review["manager_value"] == "3.5"
