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
    for path in ("/", "/runs/new", "/knowledge", "/clarifications", "/aliases", "/results"):
        response = client.get(path)
        assert response.status_code == 200


def test_create_run_and_add_knowledge(client, app):
    workspace = Path(app.config["DATABASE"]).parent
    zip_folder = workspace / "zips"
    zip_folder.mkdir()
    response = client.post(
        "/runs/new",
        data={
            "from_date": "2026-11-03",
            "to_date": "2026-11-09",
            "zip_folder": str(zip_folder),
            "budget_usd": "4",
            "stop_at_usd": "3.8",
            "sample_size": "25",
        },
    )
    assert response.status_code == 302
    database = app.extensions["cxquality_db"]
    assert database.list_runs()[0]["sample_size"] == 25
    response = client.post(
        "/knowledge",
        data={"title": "Language", "category": "service", "content": "Reply in one language."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Knowledge item added" in response.data
    assert database.list_knowledge()[0]["title"] == "Language"


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
