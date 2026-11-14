"""Offline sample preparation and budgeted comparisons, isolated from employee reports.

python -m cxquality.benchmark prepare --db local/cxquality.sqlite3 --output local/qa.jsonl
python -m cxquality.benchmark compare --dataset local/qa.jsonl --output local/comparison.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from .budget import BudgetExceeded
from .evaluation import EvidenceScoringEngine
from .ingest import IngestError, parse_zip
from .scoring import build_engine_from_env, redact_transcript_for_model


def language(text):
    ar = bool(re.search(r"[\u0600-\u06ff]", text))
    en = bool(re.search(r"[A-Za-z]", text))
    return "mixed" if ar and en else "ar" if ar else "en"


def prepare(db, output, count):
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT source_zip FROM conversations ORDER BY session_id").fetchall()
    random.Random(42).shuffle(rows)
    buckets = defaultdict(list)
    for (source,) in rows:
        try:
            chat = parse_zip(source)
        except (IngestError, OSError):
            continue
        customer = " ".join(m.text for m in chat.messages if m.role == "customer")
        group = language(customer)
        if len(buckets[group]) >= (count + 2) // 3:
            continue
        identity = hashlib.sha256(chat.session_id.encode()).hexdigest()[:16]
        buckets[group].append({
            "id": identity, "language": group, "short_request": len(customer.split()) <= 8,
            "split": "holdout" if int(identity[:2], 16) % 3 == 0 else "development",
            "text": redact_transcript_for_model(chat.normalized_text),
            "expected_status": None, "expected_mistakes": None, "reviewer_notes": "",
        })
        if sum(map(len, buckets.values())) >= count:
            break
    records = [r for values in buckets.values() for r in values][:count]
    with Path(output).open("x", encoding="utf-8") as handle:
        handle.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    return {"prepared": len(records), "languages": {k: len(v) for k, v in buckets.items()},
            "labels": "Human review required; empty labels are never model ground truth"}


def compare(dataset, output, db, budget, limit, split):
    from .quality_review import mistake_flags
    load_dotenv()
    engine = build_engine_from_env(hard_limit_usd=budget, stop_at_usd=budget * .95)
    if db:
        with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
            policies = [r[0] for r in conn.execute(
                "SELECT title || ': ' || content FROM knowledge_items WHERE active=1")]
    else:
        policies = []
    engines = {mode: EvidenceScoringEngine(engine.client, mode=mode, known_policies=policies)
               for mode in ("jev", "llm", "hybrid")}
    summary = defaultdict(lambda: {"n": 0, "labelled": 0, "status_errors": 0,
                                   "false_flags": 0, "missed_flags": 0, "flag_labelled": 0, "clarifications": 0})
    with Path(output).open("x", encoding="utf-8") as handle:
        records = [json.loads(line) for line in Path(dataset).read_text(encoding="utf-8").splitlines()]
        records = [r for r in records if r.get("split") == split][:limit]
        try:
            for row in records:
                for mode, evaluator in engines.items():
                    engine.client.reset_thread_cost()
                    result = evaluator.evaluate(row["text"])
                    cost = engine.client.consume_thread_cost()
                    flags = {f["component"] for f in mistake_flags(result.component_scores)}
                    group = summary[mode + ":" + row["language"]]
                    group["n"] += 1
                    group["clarifications"] += result.status == "needs_clarification"
                    if row.get("expected_status") is not None:
                        group["labelled"] += 1
                        group["status_errors"] += result.status != row["expected_status"]
                    if row.get("expected_mistakes") is not None:
                        group["flag_labelled"] += 1
                        expected = set(row["expected_mistakes"])
                        group["false_flags"] += len(flags - expected)
                        group["missed_flags"] += len(expected - flags)
                    handle.write(json.dumps({"id": row["id"], "mode": mode, "cost_usd": cost,
                                             "result": result.to_dict()}, ensure_ascii=False) + "\n")
                    handle.flush()
        except BudgetExceeded:
            return {"stopped": "budget", "summary": dict(summary)}
    for group in summary.values():
        if not group["labelled"]:
            group["status_errors"] = None
        if not group["flag_labelled"]:
            group["false_flags"] = group["missed_flags"] = None
    return {"summary": dict(summary), "cost_usd": engine.client.budget.snapshot().spent_usd}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "compare"])
    parser.add_argument("--db", default="local/cxquality.sqlite3")
    parser.add_argument("--dataset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    args = parser.parse_args()
    if args.action == "compare" and not args.dataset:
        parser.error("--dataset is required for compare")
    result = (prepare(args.db, args.output, args.count) if args.action == "prepare" else
              compare(args.dataset, args.output, args.db, args.budget, args.limit, args.split))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
