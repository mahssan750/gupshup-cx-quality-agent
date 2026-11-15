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
    bundle = client.get(script.group(1).decode())
    assert bundle.status_code == 200
    assert b"cxquality-theme" in bundle.data
    assert b"/auth/login" in bundle.data
    assert b"/auth/logout" in bundle.data
    stylesheet = re.search(rb'<link[^>]+href="([^"]+\.css)"', page.data)
    assert stylesheet
    css = client.get(stylesheet.group(1).decode())
    assert css.status_code == 200
    assert b"data-theme=dark" in css.data

    zips = tmp_path / "weekly-zips"
    zips.mkdir()
    (zips / "one.zip").write_bytes(b"test")
    response = client.post("/api/v1/runs", json={"zip_folder": str(zips)})
    assert response.status_code == 201
    run = app.extensions["cxquality_db"].get_run(response.get_json()["run_id"])
    assert run["zip_folder"] == str(zips.resolve())
    assert run["mode"] == "weekly"
    assert run["sample_size"] is None


def test_admin_login_protects_ui_api_and_logout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CXQUALITY_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("CXQUALITY_ADMIN_PASSWORD", "test-password-987")
    app = create_app(tmp_path / "auth.sqlite3", testing=True, auth_required=True)
    client = app.test_client()

    assert client.get("/").status_code == 200
    assert client.get("/api/v1/overview").status_code == 401
    assert client.get("/classic").status_code == 302
    status = client.get("/api/v1/auth/status").get_json()
    assert status == {
        "required": True,
        "authenticated": False,
        "username": None,
        "role": None,
    }

    rejected = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert rejected.status_code == 401
    logged_in = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-password-987"}
    )
    assert logged_in.status_code == 200
    assert logged_in.get_json()["role"] == "admin"
    cookie = logged_in.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert logged_in.headers["X-Frame-Options"] == "DENY"
    assert logged_in.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/overview").status_code == 200

    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    assert client.get("/api/v1/overview").status_code == 401


def test_admin_login_rate_limits_repeated_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CXQUALITY_ADMIN_PASSWORD", "test-password-987")
    app = create_app(tmp_path / "auth-limit.sqlite3", testing=True, auth_required=True)
    client = app.test_client()

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-password-987"}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


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


def test_confirmed_review_leaves_knowledge_unchanged_and_moves_case_to_reviewed(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "confirm.sqlite3", testing=True)
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    _conversation(db, run_id, "confirm-1")
    db.add_knowledge("Existing rule", "Ask which product the customer needs.", category="company-rule")
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "confirm-1",
            "status": "scored",
            "scores": {"company_compliance": 4, "overall": 100},
            "decision": {
                "raw_answers": {
                    "company_verdict": "compliant",
                    "applicable_reference": "Ask which product the customer needs.",
                }
            },
        }
    )
    client = app.test_client()
    before = len(db.list_knowledge(include_inactive=False))
    response = client.post(
        f"/api/v1/cases/{run_id}/confirm-1/review",
        json={
            "manager_verdict": "compliant",
            "decision": "confirmed",
            "manager_reason": "The agent followed the existing approved rule.",
            "knowledge_rule": "",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["learned"] is False
    assert len(db.list_knowledge(include_inactive=False)) == before
    assert client.get(f"/api/v1/review-sample?run_id={run_id}").get_json()["items"] == []
    reviewed = client.get(
        f"/api/v1/review-sample?run_id={run_id}&category=reviewed"
    ).get_json()["items"]
    assert reviewed[0]["reviewed"] is True
    assert reviewed[0]["manager_reason"].startswith("The agent")


def test_corrected_review_updates_verdict_and_reuses_one_learned_rule(tmp_path: Path) -> None:
    app = create_app(tmp_path / "correct.sqlite3", testing=True)
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    _conversation(db, run_id, "correct-1")
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "correct-1",
            "status": "scored",
            "scores": {"company_compliance": 0, "overall": 0},
            "decision": {"raw_answers": {"company_verdict": "non_compliant"}},
        }
    )
    client = app.test_client()
    first = client.post(
        f"/api/v1/cases/{run_id}/correct-1/review",
        json={
            "manager_verdict": "compliant",
            "decision": "confirmed",
            "manager_reason": "The response follows the company exception.",
            "knowledge_rule": "Treat this exact exception as compliant.",
        },
    )
    assert first.status_code == 200
    assert first.get_json()["learned"] is True
    corrected_item = db.scored_review_item(run_id, "correct-1")
    assert corrected_item["scores"]["overall"] == 100
    assert corrected_item["review_decision"] == "corrected"
    assert len(db.list_knowledge(include_inactive=False)) == 1

    second = client.post(
        f"/api/v1/cases/{run_id}/correct-1/review",
        json={
            "manager_verdict": "non_compliant",
            "decision": "confirmed",
            "manager_reason": "The exception does not apply after full review.",
            "knowledge_rule": "Do not apply the exception when the required condition is absent.",
        },
    )
    assert second.status_code == 200
    assert db.scored_review_item(run_id, "correct-1")["scores"]["overall"] == 0
    assert len(db.list_knowledge(include_inactive=False)) == 1


def test_review_api_rejects_invalid_verdict_before_writing(tmp_path: Path) -> None:
    app = create_app(tmp_path / "invalid-verdict.sqlite3", testing=True)
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    _conversation(db, run_id, "invalid-verdict")
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "invalid-verdict",
            "status": "scored",
            "scores": {"company_compliance": 4, "overall": 100},
        }
    )
    response = app.test_client().post(
        f"/api/v1/cases/{run_id}/invalid-verdict/review",
        json={"manager_verdict": "maybe", "manager_reason": "test"},
    )
    assert response.status_code == 400
    assert db.manual_review_map("quality") == {}


def test_run_api_validation_stop_resume_and_duplicate_protection(tmp_path: Path) -> None:
    app = create_app(tmp_path / "runs.sqlite3", testing=True)
    client = app.test_client()
    missing = client.post("/api/v1/runs", json={"zip_folder": str(tmp_path / "missing")})
    assert missing.status_code == 409

    empty = tmp_path / "empty"
    empty.mkdir()
    assert client.post("/api/v1/runs", json={"zip_folder": str(empty)}).status_code == 409

    zips = tmp_path / "zips"
    zips.mkdir()
    (zips / "one.zip").write_bytes(b"test")
    created = client.post("/api/v1/runs", json={"zip_folder": str(zips)})
    run_id = created.get_json()["run_id"]
    assert created.status_code == 201
    assert client.post("/api/v1/runs", json={"zip_folder": str(zips)}).status_code == 409
    stopped = client.post(f"/api/v1/runs/{run_id}/stop")
    assert stopped.status_code == 200
    assert stopped.get_json()["checkpoint_safe"] is True
    resumed = client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert app.extensions["cxquality_db"].get_run(run_id)["status"] == "queued"


def test_candidate_api_rejects_empty_approval_and_prevents_double_review(tmp_path: Path) -> None:
    app = create_app(tmp_path / "candidates.sqlite3", testing=True)
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(
        from_date="2026-11-10", to_date="2026-11-10", zip_folder=str(tmp_path)
    )
    _conversation(db, run_id, "gap-1")
    db.upsert_evaluation(
        {"run_id": run_id, "session_id": "gap-1", "status": "needs_clarification"}
    )
    db.queue_clarification(
        run_id=run_id,
        session_id="gap-1",
        question="What is the approved delivery rule?",
        context_summary="The customer asks about delivery.",
    )
    db.sync_knowledge_candidates(run_id)
    candidate = db.list_knowledge_candidates("pending")[0]
    client = app.test_client()
    empty = client.post(
        f"/api/v1/knowledge-candidates/{candidate['id']}/review",
        json={"decision": "approved", "rule_text": ""},
    )
    assert empty.status_code == 400
    approved = client.post(
        f"/api/v1/knowledge-candidates/{candidate['id']}/review",
        json={"decision": "approved", "rule_text": "Use the approved delivery policy."},
    )
    assert approved.status_code == 200
    assert approved.get_json()["knowledge_item_id"]
    assert client.post(
        f"/api/v1/knowledge-candidates/{candidate['id']}/review",
        json={"decision": "rejected"},
    ).status_code == 400
