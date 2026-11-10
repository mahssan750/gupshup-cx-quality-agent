"""Command-line entry point for the manual weekly workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .db import Database, import_policy_text


def _value(obj: object, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _dict(value: object) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _transcript_text(transcript: object) -> str:
    direct = _value(transcript, "normalized_text", "text", "transcript_text")
    if direct:
        return str(direct)
    messages = _value(transcript, "messages", default=[])
    lines: list[str] = []
    for message in messages:
        sender = _value(message, "sender", default="unknown")
        content = _value(message, "content", "text", default="")
        if content:
            lines.append(f"{sender}: {content}")
    return "\n".join(lines)


def _agent_alias(transcript: object) -> str | None:
    direct = _value(transcript, "assigned_agent", "agent_alias", "assigned_agent_alias")
    if direct:
        return str(direct)
    assignments = _value(transcript, "assignments", default=[])
    if assignments:
        last = assignments[-1]
        return (
            str(_value(last, "agent_alias", "agent", "agent_name", "assignee", default="")) or None
        )
    return None


def _load_roster(db: Database, roster_path: str | None):
    if not roster_path:
        return []
    from .roster import load_roster_xlsx, normalize_alias

    override_path = os.environ.get("CXQUALITY_ROSTER_OVERRIDES")
    overrides: list[dict[str, Any]] = []
    if override_path and Path(override_path).is_file():
        loaded = json.loads(Path(override_path).read_text(encoding="utf-8-sig"))
        overrides = loaded if isinstance(loaded, list) else loaded.get("agents", [])
    records = load_roster_xlsx(roster_path, manual_overrides=overrides)
    for record in records:
        agent_id = (
            record.staff_id
            or "name_"
            + hashlib.sha256(normalize_alias(record.canonical_name).encode("utf-8")).hexdigest()[
                :16
            ]
        )
        db.upsert_agent(
            {
                "id": agent_id,
                "staff_id": record.staff_id,
                "canonical_name": record.canonical_name,
                "canonical_name_en": record.canonical_name_en,
                "country": record.country,
                "branch": record.branch,
                "active": record.active,
            }
        )
    return records


def _resolve_alias(db: Database, alias: str | None, records: list[object]) -> str | None:
    if not alias or not records:
        return None
    from .roster import normalize_alias, suggest_alias

    suggestion = suggest_alias(alias, records)
    agent_id = None
    if suggestion.agent:
        agent_id = (
            suggestion.agent.staff_id
            or "name_"
            + hashlib.sha256(
                normalize_alias(suggestion.agent.canonical_name).encode("utf-8")
            ).hexdigest()[:16]
        )
    alias_id = db.suggest_alias(
        alias,
        normalize_alias(alias),
        agent_id=agent_id,
        confidence=suggestion.confidence,
        source="transcript",
    )
    # Exact aliases from the controlled roster can be accepted. Fuzzy or ambiguous matches
    # remain suggestions for the review UI and are never silently assigned.
    if suggestion.status == "matched" and suggestion.confidence >= 0.98 and agent_id:
        db.review_alias(alias_id, "accepted", agent_id)
        return agent_id
    return None


def execute_run(db: Database, run_id: str, *, no_ai: bool = False) -> None:
    """Execute or resume a run using the stable ingest/metrics/scoring interfaces."""

    run = db.get_run(run_id)
    if not run:
        raise KeyError(f"Run not found: {run_id}")
    zip_folder = Path(run["zip_folder"])
    if not zip_folder.is_dir():
        db.update_run(run_id, status="blocked", error=f"ZIP folder not found: {zip_folder}")
        raise FileNotFoundError(zip_folder)

    from .ingest import build_customer_threads, ingest_directory, zip_paths
    from .metrics import compute_metrics

    all_paths = zip_paths(zip_folder)
    thread_map = build_customer_threads(all_paths)
    # Repeat contact is based on weekly primary sessions, never on all historical PDFs that
    # happen to be included by Entire Conversation History.
    thread_counts: dict[str, int] = {}
    for path in all_paths:
        thread_id = thread_map.get(path.stem, path.stem)
        thread_counts[thread_id] = thread_counts.get(thread_id, 0) + 1
    paths = all_paths
    if run["sample_size"] and run["sample_size"] < len(all_paths):
        sample_seed = "|".join(
            (
                str(run["from_date"]),
                str(run["to_date"]),
                str(zip_folder.resolve()),
                str(run["sample_size"]),
            )
        )
        paths = random.Random(sample_seed).sample(all_paths, int(run["sample_size"]))
        paths.sort(key=lambda path: path.name)
    db.update_run(run_id, status="running", total_count=len(paths), error=None)
    checkpoint = db.checkpoint(run_id)
    completed = set(checkpoint.get("completed_session_ids", []))
    roster_records = _load_roster(db, run.get("roster_path"))

    engine = None
    knowledge = [item["content"] for item in db.list_knowledge(include_inactive=False)]
    if not no_ai:
        from .scoring import build_engine_from_env

        previous_budget = os.environ.get("CXQUALITY_BUDGET_USD")
        previous_stop = os.environ.get("CXQUALITY_STOP_AT_USD")
        os.environ["CXQUALITY_BUDGET_USD"] = str(run["budget_usd"])
        os.environ["CXQUALITY_STOP_AT_USD"] = str(run["stop_at_usd"])
        try:
            engine = build_engine_from_env(known_policies=knowledge)
        finally:
            if previous_budget is None:
                os.environ.pop("CXQUALITY_BUDGET_USD", None)
            else:
                os.environ["CXQUALITY_BUDGET_USD"] = previous_budget
            if previous_stop is None:
                os.environ.pop("CXQUALITY_STOP_AT_USD", None)
            else:
                os.environ["CXQUALITY_STOP_AT_USD"] = previous_stop

    counts = {
        "processed_count": int(run["processed_count"]),
        "scored_count": int(run["scored_count"]),
        "clarification_count": int(run["clarification_count"]),
        "excluded_count": int(run["excluded_count"]),
    }
    try:
        selected_paths = paths
        for transcript in ingest_directory(zip_folder, selected_paths=selected_paths):
            session_id = str(_value(transcript, "session_id"))
            if not session_id or session_id in completed:
                continue
            text = _transcript_text(transcript)
            thread_id = _value(transcript, "customer_thread_id") or thread_map.get(
                session_id, session_id
            )
            metrics = compute_metrics(
                transcript, weekly_sessions_in_thread=thread_counts.get(thread_id, 1)
            )
            metric_data = _dict(metrics)
            eligible = bool(metric_data.get("eligible", _value(metrics, "eligible", default=True)))
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            displayed_alias = _agent_alias(transcript)
            agent_id = _resolve_alias(db, displayed_alias, roster_records)
            db.upsert_conversation(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "source_zip": str(
                        _value(transcript, "source_zip", default=f"{session_id}.zip")
                    ),
                    "started_at": str(_value(transcript, "started_at", default="")) or None,
                    "ended_at": str(_value(transcript, "last_message_at", "ended_at", default=""))
                    or None,
                    "language": _value(transcript, "language"),
                    "assigned_agent_alias": displayed_alias,
                    "agent_id": agent_id,
                    "eligible": eligible,
                    "transcript_hash": text_hash,
                    "metrics": metric_data,
                }
            )
            evaluation = None
            if not eligible:
                evaluation = {
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": "not_scorable",
                    "model": None,
                    "scores": {},
                    "confidence": None,
                    "evidence": [],
                }
                counts["excluded_count"] += 1
            elif not no_ai:
                before_spend = engine.client.budget.snapshot().spent_usd
                result = engine.evaluate(text)
                after_spend = engine.client.budget.snapshot().spent_usd
                evaluation_cost = max(0.0, after_spend - before_spend)
                result_data = _dict(result)
                component_scores = dict(result_data.get("component_scores") or {})
                if result_data.get("quality_score") is not None:
                    component_scores["overall"] = result_data["quality_score"]
                evaluation = {
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": result_data["status"],
                    "model": result_data.get("model"),
                    "scores": component_scores,
                    "confidence": result_data.get("confidence"),
                    "evidence": result_data.get("evidence", []),
                    "clarification_question": result_data.get("clarification_question"),
                    "cost_usd": evaluation_cost,
                }
                if evaluation_cost:
                    db.record_spend(
                        {
                            "run_id": run_id,
                            "session_id": session_id,
                            "provider": "OpenRouter",
                            "model": result_data.get("model") or "mixed",
                            "request_type": "conversation-evaluation",
                            "cost_usd": evaluation_cost,
                        }
                    )
                if result_data["status"] == "scored":
                    counts["scored_count"] += 1
                elif result_data["status"] == "needs_clarification":
                    counts["clarification_count"] += 1
                    db.queue_clarification(
                        run_id=run_id,
                        session_id=session_id,
                        question=result_data.get("clarification_question")
                        or "What information is needed to score this conversation?",
                        context_summary=result_data.get("knowledge_gap")
                        or result_data.get("reason"),
                    )
                else:
                    counts["excluded_count"] += 1
            if evaluation is not None:
                db.upsert_evaluation(evaluation)
            counts["processed_count"] += 1
            completed.add(session_id)
            db.update_run(run_id, **counts)
            db.save_checkpoint(
                run_id,
                {
                    "completed_session_ids": sorted(completed),
                    "last_session_id": session_id,
                },
            )
        db.update_run(run_id, status="completed")
    except Exception as exc:
        # Budget controls may raise different concrete exception types. Do not expose keys or
        # request headers; exception messages from our clients contain only safe summaries.
        name = type(exc).__name__.lower()
        status = "stopped_budget" if "budget" in name or "budget" in str(exc).lower() else "failed"
        db.update_run(run_id, status=status, error=str(exc)[:1000], **counts)
        raise


def _add_run_arguments(parser: argparse.ArgumentParser, *, sample_default: int | None) -> None:
    parser.add_argument("--db", default=os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3"))
    parser.add_argument("--zip-folder", required=True)
    parser.add_argument("--roster")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--budget", type=float, default=4.0)
    parser.add_argument("--stop-at", type=float, default=3.8)
    parser.add_argument("--sample-size", type=int, default=sample_default)
    parser.add_argument("--no-ai", action="store_true", help="Calculate operational metrics only")
    parser.add_argument(
        "--prepare-only", action="store_true", help="Create the run without starting"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cxquality")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="Create or migrate the local database")
    init.add_argument("--db", default=os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3"))
    policy = sub.add_parser("import-policy", help="Import a UTF-8 policy text file")
    policy.add_argument("path")
    policy.add_argument("--db", default=os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3"))
    policy.add_argument("--category", default="company-policy")
    weekly = sub.add_parser("run-week", help="Run the local weekly pipeline")
    _add_run_arguments(weekly, sample_default=None)
    sample = sub.add_parser("sample", help="Run a deterministic budget-limited sample")
    _add_run_arguments(sample, sample_default=200)
    resume = sub.add_parser("resume", help="Resume a checkpointed run")
    resume.add_argument("run_id")
    resume.add_argument("--db", default=os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3"))
    resume.add_argument("--no-ai", action="store_true")
    web = sub.add_parser("serve", help="Open the local review UI")
    web.add_argument("--db", default=os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3"))
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Load the local, gitignored configuration before argparse reads environment defaults.
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        db = Database(args.db)
        db.initialize()
        print(f"Initialized local database: {db.path}")
        return 0
    if args.command == "import-policy":
        db = Database(args.db)
        db.initialize()
        count = import_policy_text(db, args.path, args.category)
        print(f"Imported {count} knowledge items")
        return 0
    if args.command == "serve":
        from .web import serve

        serve(args.db, host=args.host, port=args.port)
        return 0
    if args.command == "resume":
        db = Database(args.db)
        db.initialize()
        execute_run(db, args.run_id, no_ai=args.no_ai)
        return 0
    if args.command in {"run-week", "sample"}:
        zip_folder = Path(args.zip_folder)
        if not zip_folder.is_dir():
            print(f"ZIP folder was not found: {zip_folder}", file=sys.stderr)
            return 2
        if args.roster and not Path(args.roster).is_file():
            print(f"Roster workbook was not found: {args.roster}", file=sys.stderr)
            return 2
        if not args.no_ai and not os.environ.get("OPENROUTER_API_KEY", "").strip():
            print(
                "OPENROUTER_API_KEY is empty. Put the complete key after "
                "OPENROUTER_API_KEY= in the local .env file, then run this command again.",
                file=sys.stderr,
            )
            return 2
        db = Database(args.db)
        db.initialize()
        run_id = db.create_run(
            from_date=args.from_date,
            to_date=args.to_date,
            zip_folder=args.zip_folder,
            roster_path=args.roster,
            budget_usd=args.budget,
            stop_at_usd=args.stop_at,
            sample_size=args.sample_size,
            mode="sample" if args.command == "sample" else "weekly",
        )
        print(f"Run ID: {run_id}")
        if not args.prepare_only:
            execute_run(db, run_id, no_ai=args.no_ai)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
