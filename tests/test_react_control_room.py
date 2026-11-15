from __future__ import annotations

import re
from pathlib import Path

from cxquality.db import Database
from cxquality.web import create_app


def _conversation(db: Database, run_id: str, session_id: str) -> None:
    db.upsert_conversation(
        {
            "session_id": session_id,
            "run_id": run_id,
            "source_zip": f"{session_id}.zip",
            "transcript_hash": f"hash-{session_id}",
            "eligible": True,
            "assigned_agent_alias": "Agent One",
        }
    )


def test_react_shell_and_one_field_run_api(tmp_path: Path) -> None:
    app = create_app(tmp_path / "ui.sqlite3", testing=True)
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert b'<div id="root"></div>' in page.data
    script = re.search(rb'<script[^>]+src="([^"]+\.js)"', page.data)
    assert script
    assert client.get(script.group(1).decode()).status_code == 200

    zips = tmp_path / "weekly-zips"
    zips.mkdir()
    (zips / "one.zip").write_bytes(b"test")
    response = client.post("/api/v1/runs", json={"zip_folder": str(zips)})
    assert response.status_code == 201
    run = app.extensions["cxquality_db"].get_run(response.get_json()["run_id"])
    assert run["zip_folder"] == str(zips.resolve())
    assert run["mode"] == "weekly"
    assert run["sample_size"] is None


def test_completed_run_creates_review_draft_before_knowledge_activation(tmp_path: Path) -> None:
    db = Database(tmp_path / "loop.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-10",
        to_date="2026-11-10",
        zip_folder=str(tmp_path),
    )
    _conversation(db, run_id, "branch-gap")
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "branch-gap",
            "status": "needs_clarification",
            "evidence": ["Customer : Where is the nearest branch?", "Agent : Which country?"],
        }
    )
    db.queue_clarification(
        run_id=run_id,
        session_id="branch-gap",
        question="What is the approved branch-location behavior?",
        context_summary="The customer asks for the nearest branch.",
    )
    assert db.sync_knowledge_candidates(run_id) == 1
    candidate = db.list_knowledge_candidates("pending")[0]
    assert candidate["theme"] == "branch_location"
    assert candidate["occurrence_count"] == 1
    assert db.list_knowledge(include_inactive=False) == []

    knowledge_id = db.review_knowledge_candidate(
        candidate["id"],
        decision="approved",
        rule_text="Ask for the country, then provide the nearest approved branch.",
    )
    assert knowledge_id
    knowledge = db.list_knowledge(include_inactive=False)[0]
    assert knowledge["category"] == "company-rule"


def test_representative_api_explains_company_verdict(tmp_path: Path) -> None:
    app = create_app(tmp_path / "sample.sqlite3", testing=True)
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(
        from_date="2026-11-10",
        to_date="2026-11-10",
        zip_folder=str(tmp_path),
    )
    _conversation(db, run_id, "case-1")
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "case-1",
            "status": "scored",
            "scores": {"company_compliance": 0, "overall": 0},
            "confidence": 0.83,
            "evidence": ["Customer : What is the total?", "Agent : Product price only"],
            "decision": {
                "raw_answers": {
                    "company_verdict": "non_compliant",
                    "applicable_reference": "Calculate the total including shipping.",
                    "component_evidence": {
                        "customer": "Customer : What is the total?",
                        "agent": "Agent : Product price only",
                    },
                }
            },
        }
    )
    payload = app.test_client().get(f"/api/v1/review-sample?run_id={run_id}").get_json()
    item = payload["items"][0]
    assert item["category"] == "non_compliant"
    assert item["reference"] == "Calculate the total including shipping."
    assert "مخالفة مباشرة" in item["explanation"]
