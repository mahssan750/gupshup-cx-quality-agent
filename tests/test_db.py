from __future__ import annotations

import pytest

from cxquality.db import Database, import_policy_text


def make_db(tmp_path):
    db = Database(tmp_path / "quality.sqlite3")
    db.initialize()
    return db


def seed_conversation(db: Database, run_id: str, session_id: str = "123"):
    db.upsert_conversation(
        {
            "session_id": session_id,
            "run_id": run_id,
            "source_zip": f"{session_id}.zip",
            "transcript_hash": "abc",
            "eligible": True,
            "assigned_agent_alias": "Agent One",
            "metrics": {"eligible": True},
        }
    )


def test_run_checkpoint_and_budget_validation(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(ValueError):
        db.create_run(
            from_date="2026-11-03",
            to_date="2026-11-09",
            zip_folder="zips",
            budget_usd=1,
            stop_at_usd=2,
        )
    run_id = db.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips", sample_size=20
    )
    db.save_checkpoint(run_id, {"last_session_id": "42"})
    assert db.get_run(run_id)["mode"] == "sample"
    assert db.checkpoint(run_id) == {"last_session_id": "42"}


def test_answer_clarification_learns_and_queues_similar_questions(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips")
    seed_conversation(db, run_id, "123")
    seed_conversation(db, run_id, "456")
    for session in ("123", "456"):
        db.upsert_evaluation(
            {"run_id": run_id, "session_id": session, "status": "needs_clarification"}
        )
    clarification_id = db.queue_clarification(
        run_id=run_id, session_id="123", question="Was the issue resolved?"
    )
    db.queue_clarification(
        run_id=run_id, session_id="456", question="Was the issue resolved?"
    )
    knowledge_id = db.answer_clarification(clarification_id, "Yes, after the refund.")
    assert knowledge_id is not None
    with db.connect() as conn:
        statuses = dict(
            conn.execute("SELECT session_id,status FROM evaluations ORDER BY session_id").fetchall()
        )
    assert statuses == {"123": "queued_for_rescore", "456": "queued_for_rescore"}
    items = db.list_clarifications()
    assert all(item["status"] == "answered" for item in items)
    assert all(item["rescore_required"] == 1 for item in items)
    assert db.clarification_learning_count(knowledge_id) == 2
    assert "Approved answer: Yes, after the refund." in db.list_knowledge()[0]["content"]


def test_grouped_clarifications_include_evidence_and_learn_once(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips")
    for session, question in (
        ("101", "Where is the nearest branch?"),
        ("102", "Which country and location is the customer asking about?"),
    ):
        seed_conversation(db, run_id, session)
        db.upsert_evaluation(
            {
                "run_id": run_id,
                "session_id": session,
                "status": "needs_clarification",
                "evidence": ["Customer : Where is your nearest branch?", "Agent : Dubai Mall"],
            }
        )
        db.queue_clarification(run_id=run_id, session_id=session, question=question)
    groups = db.list_clarification_groups()
    branch = next(group for group in groups if group["key"] == "branch_location")
    assert branch["count"] == 2
    assert branch["examples"][0]["evidence"]
    knowledge_id, learned = db.answer_clarification_group(
        "branch_location", "Providing the correct branch location is sufficient."
    )
    assert learned == 2
    assert db.clarification_learning_count(knowledge_id) == 2
    with db.connect() as conn:
        statuses = dict(
            conn.execute("SELECT session_id,status FROM evaluations ORDER BY session_id").fetchall()
        )
    assert statuses == {"101": "queued_for_rescore", "102": "queued_for_rescore"}


def test_revising_answer_updates_existing_knowledge_and_linked_records(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips")
    seed_conversation(db, run_id, "revise-1")
    db.upsert_evaluation(
        {"run_id": run_id, "session_id": "revise-1", "status": "needs_clarification"}
    )
    clarification_id = db.queue_clarification(
        run_id=run_id, session_id="revise-1", question="Was this greeting acceptable?"
    )
    knowledge_id = db.answer_clarification(clarification_id, "Yes, after two hours.")
    assert db.revise_clarification_answer(clarification_id, "Yes, after one hour.") == 1
    knowledge = next(item for item in db.list_knowledge() if item["id"] == knowledge_id)
    assert "Approved answer: Yes, after one hour." in knowledge["content"]
    assert "two hours" not in knowledge["content"]
    item = db.list_clarifications("answered")[0]
    assert item["answer"] == "Yes, after one hour."


def test_dashboard_coverage_and_spend(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips")
    for session, status in (("1", "scored"), ("2", "needs_clarification"), ("3", "not_scorable")):
        seed_conversation(db, run_id, session)
        db.upsert_evaluation(
            {
                "run_id": run_id,
                "session_id": session,
                "status": status,
                "scores": {"overall": 75} if status == "scored" else {},
            }
        )
    db.record_spend(
        {
            "run_id": run_id,
            "provider": "TypeSafe",
            "model": "typesafe/jev-1.14",
            "request_type": "decision",
            "cost_usd": 0.01,
        }
    )
    summary = db.dashboard(run_id)
    assert summary["coverage"]["scoring_coverage"] == pytest.approx(0.5)
    assert summary["coverage"]["clarification_rate"] == pytest.approx(0.5)
    assert summary["coverage"]["exclusion_rate"] == pytest.approx(1 / 3)
    assert summary["run"]["cost_usd"] == pytest.approx(0.01)


def test_ranking_adjusts_small_samples_and_requires_minimum_volume(tmp_path):
    db = make_db(tmp_path)
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="zips")
    values = [("one", "One Chat Agent", 100.0)] + [
        (f"many-{index}", "Many Chat Agent", 60.0) for index in range(10)
    ]
    for session, alias, score in values:
        db.upsert_conversation(
            {
                "session_id": session,
                "run_id": run_id,
                "source_zip": f"{session}.zip",
                "transcript_hash": session,
                "eligible": True,
                "assigned_agent_alias": alias,
            }
        )
        db.upsert_evaluation(
            {
                "run_id": run_id,
                "session_id": session,
                "status": "scored",
                "scores": {"overall": score},
            }
        )
    rows = {row["agent"]: row for row in db.dashboard(run_id)["agent_results"]}
    assert rows["One Chat Agent"]["average_score"] == 100
    assert rows["One Chat Agent"]["ranking_score"] < 70
    assert rows["One Chat Agent"]["rank"] is None
    assert rows["Many Chat Agent"]["rank"] == 1
    assert rows["Many Chat Agent"]["processed"] == 10


def test_alias_acceptance_consolidates_duplicate_suggestions(tmp_path):
    db = make_db(tmp_path)
    db.upsert_agent({"id": "1924", "canonical_name": "Taha Elsayed"})
    accepted_id = db.suggest_alias(
        "TAHA ELSAYED", "tahaelsayed", agent_id="1924", confidence=0.8
    )
    db.review_alias(accepted_id, "accepted", "1924")
    with db.transaction() as conn:
        duplicate_id = conn.execute(
            """INSERT INTO aliases
            (alias,normalized_alias,agent_id,confidence,source,status,created_at)
            VALUES ('TAHA ELSAYED','tahaelsayed',NULL,0.8,'transcript','suggested',?)""",
            ("2026-11-10T00:00:00+00:00",),
        ).lastrowid
    db.review_alias(int(duplicate_id), "accepted", "1924")
    aliases = db.list_aliases()
    assert sum(item["status"] == "accepted" for item in aliases) == 1
    assert db.accepted_agent_for_alias("tahaelsayed") == "1924"


def test_import_numbered_policy(tmp_path):
    db = make_db(tmp_path)
    policy = tmp_path / "policy.txt"
    policy.write_text("Training\n1- First rule\n2- Second rule\ncontinued", encoding="utf-8")
    assert import_policy_text(db, policy) == 2
    assert [item["title"] for item in db.list_knowledge()] == ["Policy 1", "Policy 2"]
