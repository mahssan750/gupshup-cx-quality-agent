from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from cxquality import ingest, metrics
from cxquality.cli import (
    _agent_alias,
    _load_roster,
    _resolve_alias,
    build_parser,
    execute_run,
    main,
)
from cxquality.db import Database
from cxquality.ingest import AssignmentEvent


def test_init_db(tmp_path):
    path = tmp_path / "cli.sqlite3"
    assert main(["init-db", "--db", str(path)]) == 0
    assert path.exists()


def test_import_policy_and_prepare_sample(tmp_path):
    path = tmp_path / "cli.sqlite3"
    policy = tmp_path / "policy.txt"
    policy.write_text("1- Respond quickly\n2- Reply in the customer's language", encoding="utf-8")
    assert main(["import-policy", str(policy), "--db", str(path)]) == 0
    assert (
        main(
            [
                "sample",
                "--db",
                str(path),
                "--zip-folder",
                str(tmp_path / "zips"),
                "--from-date",
                "2026-11-03",
                "--to-date",
                "2026-11-09",
                "--sample-size",
                "12",
                "--prepare-only",
            ]
        )
        == 0
    )
    db = Database(path)
    assert len(db.list_knowledge()) == 2
    run = db.list_runs()[0]
    assert run["status"] == "queued"
    assert run["sample_size"] == 12


def test_sample_default_is_200():
    args = build_parser().parse_args(
        [
            "sample",
            "--zip-folder",
            "zips",
            "--from-date",
            "2026-11-03",
            "--to-date",
            "2026-11-09",
        ]
    )
    assert args.sample_size == 200


def test_assignment_event_agent_alias_is_used():
    transcript = SimpleNamespace(
        assignments=(
            AssignmentEvent(datetime(2026, 11, 3, 10), "Display Agent", "manager", "DEFAULT"),
        )
    )
    assert _agent_alias(transcript) == "Display Agent"


def test_exact_roster_alias_is_accepted_but_fuzzy_stays_for_review(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["الموظف", "الرقم الوظيفي", "الدولة", "الفرع", "alias"])
    sheet.append(["محمد إبراهيم", 1927, "الامارات", "القناة", "qanadnp"])
    roster_path = tmp_path / "roster.xlsx"
    workbook.save(roster_path)
    db = Database(tmp_path / "aliases.sqlite3")
    db.initialize()
    records = _load_roster(db, str(roster_path))
    assert _resolve_alias(db, "qanadnp", records) == "1927"
    assert _resolve_alias(db, "completely unrelated", records) is None
    aliases = {item["alias"]: item["status"] for item in db.list_aliases()}
    assert aliases["qanadnp"] == "accepted"
    assert aliases["completely unrelated"] == "suggested"


def test_no_ai_keeps_eligible_conversation_unscored_and_uses_full_week_threads(
    tmp_path, monkeypatch
):
    zips = tmp_path / "zips"
    zips.mkdir()
    all_paths = [zips / "100.zip", zips / "101.zip"]
    for path in all_paths:
        path.write_bytes(b"synthetic")
    thread_map = {"100": "thread_x", "101": "thread_x"}
    seen = {}

    monkeypatch.setattr(ingest, "zip_paths", lambda _folder: all_paths)

    def fake_threads(paths):
        seen["thread_paths"] = [Path(item).name for item in paths]
        return thread_map

    monkeypatch.setattr(ingest, "build_customer_threads", fake_threads)

    def fake_ingest(_folder, *, selected_paths):
        for path in selected_paths:
            sid = Path(path).stem
            yield SimpleNamespace(
                session_id=sid,
                source_zip=path,
                normalized_text="Customer: I need help\nAgent: I can help with that request.",
                assignments=(
                    AssignmentEvent(datetime(2026, 11, 3, 10), "Agent Exact", "manager", "DEFAULT"),
                ),
                customer_thread_id=thread_map[sid],
                started_at=datetime(2026, 11, 3, 9),
                last_message_at=datetime(2026, 11, 3, 10),
                language="en",
            )

    monkeypatch.setattr(ingest, "ingest_directory", fake_ingest)

    class FakeMetrics:
        eligible = True

        def __init__(self, weekly):
            self.weekly = weekly

        def as_dict(self):
            return {
                "eligible": True,
                "weekly_sessions_in_thread": self.weekly,
                "repeat_contact": self.weekly > 1,
            }

    def fake_compute(_transcript, *, weekly_sessions_in_thread):
        seen["weekly_count"] = weekly_sessions_in_thread
        return FakeMetrics(weekly_sessions_in_thread)

    monkeypatch.setattr(metrics, "compute_metrics", fake_compute)
    db = Database(tmp_path / "run.sqlite3")
    db.initialize()
    run_id = db.create_run(
        from_date="2026-11-03",
        to_date="2026-11-09",
        zip_folder=str(zips),
        sample_size=1,
    )
    execute_run(db, run_id, no_ai=True)
    assert seen["thread_paths"] == ["100.zip", "101.zip"]
    assert seen["weekly_count"] == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0
        row = conn.execute("SELECT metrics_json,assigned_agent_alias FROM conversations").fetchone()
    assert json.loads(row["metrics_json"])["repeat_contact"] is True
    assert row["assigned_agent_alias"] == "Agent Exact"
