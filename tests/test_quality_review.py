from cxquality.db import Database
from cxquality.quality_review import mistake_flags, normalized_component_scores


def _scored_chat(db: Database):
    run_id = db.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips"
    )
    db.save_checkpoint(run_id, {"evaluation_version": "company-supervisor-v1"})
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
                "company_compliance": 0,
                "overall": 0,
                "_rubric_version": 3,
            },
            "evidence": ["Customer : still waiting", "Agent One : check later"],
            "confidence": 0.8,
            "decision": {"raw_answers": {"company_verdict": "non_compliant"}},
        }
    )
    return run_id


def test_non_compliant_verdict_is_the_only_mistake_flag():
    scores = normalized_component_scores({"company_compliance": 0})
    flags = mistake_flags(scores)
    assert [item["component"] for item in flags] == ["company_compliance"]


def test_company_verdict_override_updates_knowledge_and_audit(tmp_path):
    db = Database(tmp_path / "quality.sqlite3")
    db.initialize()
    run_id = _scored_chat(db)
    review_id = db.apply_manual_review(
        run_id=run_id,
        session_id="qa-1",
        review_kind="quality",
        component="overall",
        decision="corrected",
        corrected_overall=100,
        manager_reason="The agent followed the applicable company rule.",
        knowledge_rule="This response is compliant when the same conditions are present.",
    )
    item = db.scored_review_item(run_id, "qa-1")
    assert item["scores"]["company_compliance"] == 4
    assert item["scores"]["overall"] == 100
    assert item["review_decision"] == "corrected"
    assert db.manual_review_history(review_id)[0]["manager_reason"].startswith("The agent")
    learned = [row for row in db.list_knowledge() if row["source"] == "manager-review"]
    assert len(learned) == 1


def test_mistake_override_preserves_original_company_verdict(tmp_path):
    db = Database(tmp_path / "mistake.sqlite3")
    db.initialize()
    run_id = _scored_chat(db)
    db.apply_manual_review(
        run_id=run_id,
        session_id="qa-1",
        review_kind="mistake",
        component="company_compliance",
        decision="dismissed",
        corrected_overall=100,
        manager_reason="The applicable company rule was followed.",
        knowledge_rule="Treat this pattern as compliant under the same conditions.",
    )
    item = db.scored_review_item(run_id, "qa-1")
    review = db.manual_review_map("mistake")[(run_id, "qa-1", "company_compliance")]
    assert item["scores"]["company_compliance"] == 4
    assert review["ai_value"] == "0"
    assert review["manager_value"] == "4.0"
