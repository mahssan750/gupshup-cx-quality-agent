from __future__ import annotations

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
    response = client.post(
        "/runs/new",
        data={
            "from_date": "2026-11-03",
            "to_date": "2026-11-09",
            "zip_folder": r"C:\exports\zips",
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
    assert b"Only this conversation was queued" in response.data


def test_serve_rejects_non_loopback(tmp_path):
    with pytest.raises(ValueError):
        serve(tmp_path / "db.sqlite3", host="0.0.0.0")
