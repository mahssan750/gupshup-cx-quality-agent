from __future__ import annotations

from pathlib import Path

import pytest

from cxquality.db import Database
from cxquality.web import create_app, serve


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "web.sqlite3", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


def test_main_pages_render(client):
    for path in (
        "/",
        "/runs",
        "/runs/new",
        "/control-center",
        "/knowledge",
        "/clarifications",
        "/aliases",
        "/results",
    ):
        response = client.get(path)
        assert response.status_code == 200


def test_create_run_and_add_knowledge(client, app):
    workspace = Path(app.config["DATABASE"]).parent
    zip_folder = workspace / "zips"
    zip_folder.mkdir()
    (zip_folder / "1.zip").write_bytes(b"test")
    response = client.post(
        "/runs/new",
        data={"zip_folder": str(zip_folder)},
    )
    assert response.status_code == 302
    database = app.extensions["cxquality_db"]
    assert database.list_runs()[0]["sample_size"] is None
    response = client.post(
        "/knowledge",
        data={"title": "Language", "category": "service", "content": "Reply in one language."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Knowledge item added" in response.data
    assert database.list_knowledge()[0]["title"] == "Language"


def test_advanced_run_controls_prepare_without_starting(client, app, tmp_path):
    zips = tmp_path / "export" / "zips"
    zips.mkdir(parents=True)
    for number in range(3):
        (zips / f"{number}.zip").write_bytes(b"test")
    response = client.post(
        "/runs/new",
        data={
            "zip_folder": str(zips),
            "date_mode": "manual",
            "from_date": "2026-11-01",
            "to_date": "2026-11-02",
            "mode": "sample",
            "sample_size": "2",
            "budget_usd": "5",
            "stop_at_usd": "4.5",
            "ai_workers": "3",
            "decision_model": "typesafe/jev-1.14",
            "writer_model": "openai/gpt-6-luna",
            "no_ai": "1",
            "prepare_only": "1",
        },
    )
    assert response.status_code == 302
    database: Database = app.extensions["cxquality_db"]
    run = database.list_runs()[0]
    settings = database.checkpoint(run["id"])["settings"]
    assert (run["from_date"], run["to_date"], run["sample_size"]) == (
        "2026-11-01",
        "2026-11-02",
        2,
    )
    assert settings["no_ai"] is True
    assert settings["ai_workers"] == 3
    assert settings["prepared"] is True


def test_run_can_be_stopped_and_resumed_from_ui(client, app):
    database: Database = app.extensions["cxquality_db"]
    run_id = database.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder="z"
    )
    database.save_checkpoint(run_id, {"settings": {"prepared": False}})
    database.update_run(run_id, status="running")
    response = client.post(f"/runs/{run_id}/stop", follow_redirects=True)
    assert response.status_code == 200
    assert database.user_stop_requested(run_id) is True
    response = client.post(f"/runs/{run_id}/resume", follow_redirects=True)
    assert response.status_code == 200
    assert database.get_run(run_id)["status"] == "queued"
    assert database.checkpoint(run_id)["settings"]["prepared"] is False


def test_duplicate_active_folder_is_rejected(client, app, tmp_path):
    zips = tmp_path / "zips"
    zips.mkdir()
    (zips / "1.zip").write_bytes(b"test")
    database: Database = app.extensions["cxquality_db"]
    database.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder=str(zips.resolve())
    )
    response = client.post(
        "/runs/new", data={"zip_folder": str(zips)}, follow_redirects=True
    )
    assert b"already has an active or resumable run" in response.data


def test_quality_review_tabs_show_scored_chat_and_ai_reasoning(client, app, tmp_path):
    database: Database = app.extensions["cxquality_db"]
    run_id = database.create_run(
        from_date="2026-11-03", to_date="2026-11-09", zip_folder=str(tmp_path)
    )
    database.upsert_agent(
        {"id": "1998", "staff_id": "1998", "canonical_name": "Agent One", "branch": "Mall"}
    )
    database.upsert_conversation(
        {
            "session_id": "review-web",
            "run_id": run_id,
            "source_zip": str(tmp_path / "missing.zip"),
            "transcript_hash": "hash",
            "eligible": True,
            "agent_id": "1998",
            "assigned_agent_alias": "Agent One",
        }
    )
    database.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "review-web",
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
            "decision": {"sentiment_end": "negative"},
        }
    )
    mistakes = client.get("/employee-mistakes")
    quality = client.get("/quality-review")
    detail = client.get(f"/quality-review/{run_id}/review-web")
    assert mistakes.status_code == quality.status_code == detail.status_code == 200
    assert b"Employee mistake dashboard" in mistakes.data
    assert b"Agent One" in mistakes.data
    assert b"Quality double check" in quality.data
    assert b"AI segmentation and why" in detail.data
    assert b"Customer : still waiting" in detail.data


def test_clarification_answer_route(client, app):
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="z")
    db.upsert_conversation(
        {
            "session_id": "99",
            "run_id": run_id,
            "source_zip": "99.zip",
            "transcript_hash": "hash",
            "eligible": True,
        }
    )
    db.upsert_evaluation({"run_id": run_id, "session_id": "99", "status": "needs_clarification"})
    item_id = db.queue_clarification(
        run_id=run_id, session_id="99", question="Did the customer accept the solution?"
    )
    response = client.post(
        f"/clarifications/{item_id}/answer",
        data={"answer": "The final customer reply confirms acceptance.", "add_to_knowledge": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Answer learned and saved to Knowledge" in response.data


def test_grouped_clarification_page_shows_evidence_and_answers_pattern(client, app):
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="z")
    db.upsert_conversation(
        {
            "session_id": "branch-1",
            "run_id": run_id,
            "source_zip": "branch-1.zip",
            "transcript_hash": "hash",
            "eligible": True,
            "assigned_agent_alias": "Agent One",
        }
    )
    db.upsert_evaluation(
        {
            "run_id": run_id,
            "session_id": "branch-1",
            "status": "needs_clarification",
            "evidence": ["Customer : Where is the nearest branch?", "Agent : Dubai Mall"],
        }
    )
    db.queue_clarification(
        run_id=run_id, session_id="branch-1", question="Where is the nearest branch?"
    )
    page = client.get("/clarifications")
    assert page.status_code == 200
    assert b"Branch, country, or location" in page.data
    assert b"Customer/agent evidence" in page.data
    response = client.post(
        "/clarifications/groups/branch_location/answer",
        data={"answer": "Provide the correct branch location."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"applied to 1 conversation" in response.data


def test_answered_clarification_can_update_existing_knowledge(client, app):
    db: Database = app.extensions["cxquality_db"]
    run_id = db.create_run(from_date="2026-11-03", to_date="2026-11-09", zip_folder="z")
    db.upsert_conversation(
        {
            "session_id": "revise-web",
            "run_id": run_id,
            "source_zip": "revise-web.zip",
            "transcript_hash": "hash",
            "eligible": True,
        }
    )
    db.upsert_evaluation(
        {"run_id": run_id, "session_id": "revise-web", "status": "needs_clarification"}
    )
    item_id = db.queue_clarification(
        run_id=run_id, session_id="revise-web", question="Was this acceptable?"
    )
    db.answer_clarification(item_id, "Old answer")
    page = client.get("/clarifications?status=answered")
    assert b"Revise approved answer" in page.data
    response = client.post(
        f"/clarifications/{item_id}/revise",
        data={"answer": "Updated answer"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Knowledge was updated" in response.data


def test_reject_then_accept_duplicate_alias_does_not_return_500(client, app):
    db: Database = app.extensions["cxquality_db"]
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
    assert client.post(f"/aliases/{duplicate_id}/reject").status_code == 302
    response = client.post(
        f"/aliases/{duplicate_id}/accept",
        data={"agent_id": "1924"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Alias accepted and existing conversations were updated" in response.data


def test_serve_rejects_non_loopback(tmp_path):
    with pytest.raises(ValueError):
        serve(tmp_path / "db.sqlite3", host="0.0.0.0")
