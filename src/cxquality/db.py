"""SQLite persistence for local CX quality runs.

The database intentionally has no column for API keys. Customer transcripts and staff
information stay in the user-selected local database and are never required by the web UI.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RANKING_PRIOR_STRENGTH = 10
MIN_RANKED_CONVERSATIONS = 5


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('queued','running','completed','failed','stopped_budget','blocked')),
    mode TEXT NOT NULL CHECK(mode IN ('weekly','sample')),
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    zip_folder TEXT NOT NULL,
    roster_path TEXT,
    budget_usd REAL NOT NULL DEFAULT 4.0 CHECK(budget_usd >= 0),
    stop_at_usd REAL NOT NULL DEFAULT 3.8 CHECK(stop_at_usd >= 0),
    cost_usd REAL NOT NULL DEFAULT 0 CHECK(cost_usd >= 0),
    total_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    scored_count INTEGER NOT NULL DEFAULT 0,
    clarification_count INTEGER NOT NULL DEFAULT 0,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    sample_size INTEGER,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    staff_id TEXT,
    canonical_name TEXT NOT NULL,
    canonical_name_en TEXT,
    country TEXT,
    branch TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    agent_id TEXT,
    confidence REAL NOT NULL DEFAULT 0 CHECK(confidence >= 0 AND confidence <= 1),
    source TEXT NOT NULL DEFAULT 'transcript',
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK(status IN ('suggested','accepted','rejected')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY(agent_id) REFERENCES agents(id),
    UNIQUE(normalized_alias, agent_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    thread_id TEXT,
    source_zip TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    language TEXT,
    assigned_agent_alias TEXT,
    agent_id TEXT,
    eligible INTEGER NOT NULL DEFAULT 0 CHECK(eligible IN (0,1)),
    transcript_hash TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    processing_status TEXT NOT NULL DEFAULT 'parsed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(agent_id) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_run ON conversations(run_id);
CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations(agent_id);
CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('scored','needs_clarification','not_scorable','queued_for_rescore','failed')),
    model TEXT,
    scores_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    clarification_question TEXT,
    cost_usd REAL NOT NULL DEFAULT 0 CHECK(cost_usd >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(session_id) REFERENCES conversations(session_id),
    UNIQUE(run_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_evaluations_run_status ON evaluations(run_id, status);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarification_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    context_summary TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','answered','ignored')),
    answer TEXT,
    knowledge_item_id INTEGER,
    rescore_required INTEGER NOT NULL DEFAULT 0 CHECK(rescore_required IN (0,1)),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(session_id) REFERENCES conversations(session_id),
    FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id),
    UNIQUE(run_id, session_id, question)
);

CREATE INDEX IF NOT EXISTS idx_clarification_status ON clarification_queue(status, run_id);

CREATE TABLE IF NOT EXISTS spend_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_type TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL CHECK(cost_usd >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
);
"""


class Database:
    """Small repository wrapper with explicit transactions and dict-like rows."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(SCHEMA)

    def create_run(
        self,
        *,
        from_date: str,
        to_date: str,
        zip_folder: str,
        roster_path: str | None = None,
        budget_usd: float = 4.0,
        stop_at_usd: float = 3.8,
        sample_size: int | None = None,
        mode: str | None = None,
    ) -> str:
        if stop_at_usd > budget_usd:
            raise ValueError("stop_at_usd cannot exceed budget_usd")
        run_id = str(uuid.uuid4())
        now = utc_now()
        run_mode = mode or ("sample" if sample_size else "weekly")
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO runs (
                    id,created_at,updated_at,status,mode,from_date,to_date,zip_folder,
                    roster_path,budget_usd,stop_at_usd,sample_size
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    now,
                    now,
                    "queued",
                    run_mode,
                    from_date,
                    to_date,
                    zip_folder,
                    roster_path,
                    budget_usd,
                    stop_at_usd,
                    sample_size,
                ),
            )
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run(self, run_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "cost_usd",
            "total_count",
            "processed_count",
            "scored_count",
            "clarification_count",
            "excluded_count",
            "error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported run fields: {sorted(unknown)}")
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key} = ?" for key in values)
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE runs SET {assignments} WHERE id = ?",
                (*values.values(), run_id),
            )

    def save_checkpoint(self, run_id: str, checkpoint: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE runs SET checkpoint_json = ?, updated_at = ? WHERE id = ?",
                (_json(checkpoint), utc_now(), run_id),
            )

    def checkpoint(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        return json.loads(run["checkpoint_json"] or "{}")

    def upsert_conversation(self, record: Mapping[str, Any]) -> None:
        required = {"session_id", "run_id", "source_zip", "transcript_hash"}
        missing = required - set(record)
        if missing:
            raise ValueError(f"Missing conversation fields: {sorted(missing)}")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO conversations (
                    session_id,run_id,thread_id,source_zip,started_at,ended_at,language,
                    assigned_agent_alias,agent_id,eligible,transcript_hash,metrics_json,
                    processing_status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    run_id=excluded.run_id,thread_id=excluded.thread_id,
                    source_zip=excluded.source_zip,started_at=excluded.started_at,
                    ended_at=excluded.ended_at,language=excluded.language,
                    assigned_agent_alias=excluded.assigned_agent_alias,
                    agent_id=excluded.agent_id,eligible=excluded.eligible,
                    transcript_hash=excluded.transcript_hash,
                    metrics_json=excluded.metrics_json,
                    processing_status=excluded.processing_status,updated_at=excluded.updated_at""",
                (
                    record["session_id"],
                    record["run_id"],
                    record.get("thread_id"),
                    record["source_zip"],
                    record.get("started_at"),
                    record.get("ended_at"),
                    record.get("language"),
                    record.get("assigned_agent_alias"),
                    record.get("agent_id"),
                    int(bool(record.get("eligible"))),
                    record["transcript_hash"],
                    _json(record.get("metrics", {})),
                    record.get("processing_status", "parsed"),
                    now,
                    now,
                ),
            )

    def upsert_evaluation(self, record: Mapping[str, Any]) -> None:
        required = {"run_id", "session_id", "status"}
        missing = required - set(record)
        if missing:
            raise ValueError(f"Missing evaluation fields: {sorted(missing)}")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO evaluations (
                    run_id,session_id,status,model,scores_json,evidence_json,confidence,
                    clarification_question,cost_usd,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,session_id) DO UPDATE SET
                    status=excluded.status,model=excluded.model,scores_json=excluded.scores_json,
                    evidence_json=excluded.evidence_json,confidence=excluded.confidence,
                    clarification_question=excluded.clarification_question,
                    cost_usd=excluded.cost_usd,updated_at=excluded.updated_at""",
                (
                    record["run_id"],
                    record["session_id"],
                    record["status"],
                    record.get("model"),
                    _json(record.get("scores", {})),
                    _json(record.get("evidence", [])),
                    record.get("confidence"),
                    record.get("clarification_question"),
                    float(record.get("cost_usd", 0)),
                    now,
                    now,
                ),
            )

    def add_knowledge(
        self, title: str, content: str, *, category: str = "general", source: str = "manual"
    ) -> int:
        title, content = title.strip(), content.strip()
        if not title or not content:
            raise ValueError("Knowledge title and content are required")
        now = utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO knowledge_items
                (title,content,category,source,active,created_at,updated_at)
                VALUES (?,?,?,?,1,?,?)""",
                (title, content, category.strip() or "general", source, now, now),
            )
            return int(cur.lastrowid)

    def update_knowledge(self, item_id: int, **values: Any) -> None:
        allowed = {"title", "content", "category", "active"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported knowledge fields: {sorted(unknown)}")
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key} = ?" for key in values)
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE knowledge_items SET {assignments} WHERE id = ?",
                (*values.values(), item_id),
            )

    def list_knowledge(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        where = "" if include_inactive else "WHERE active = 1"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM knowledge_items {where} ORDER BY category,title"
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_clarification(
        self,
        *,
        run_id: str,
        session_id: str,
        question: str,
        context_summary: str | None = None,
    ) -> int:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO clarification_queue
                (run_id,session_id,question,context_summary,status,created_at)
                VALUES (?,?,?,?, 'pending', ?)""",
                (run_id, session_id, question.strip(), context_summary, now),
            )
            row = conn.execute(
                """SELECT id FROM clarification_queue
                WHERE run_id=? AND session_id=? AND question=?""",
                (run_id, session_id, question.strip()),
            ).fetchone()
            return int(row["id"])

    def list_clarifications(self, status: str | None = None) -> list[dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if status:
            where, params = "WHERE q.status = ?", (status,)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT q.*, c.assigned_agent_alias, c.language
                FROM clarification_queue q
                JOIN conversations c ON c.session_id=q.session_id
                {where} ORDER BY q.created_at DESC""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def answer_clarification(
        self, clarification_id: int, answer: str, *, add_to_knowledge: bool = True
    ) -> int | None:
        answer = answer.strip()
        if not answer:
            raise ValueError("An answer is required")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM clarification_queue WHERE id = ?", (clarification_id,)
            ).fetchone()
            if not row:
                raise KeyError(clarification_id)
            knowledge_id: int | None = None
            if add_to_knowledge:
                cur = conn.execute(
                    """INSERT INTO knowledge_items
                    (title,content,category,source,active,created_at,updated_at)
                    VALUES (?,?,?,?,1,?,?)""",
                    (
                        f"Clarification: {row['question'][:100]}",
                        answer,
                        "clarification",
                        "clarification-inbox",
                        now,
                        now,
                    ),
                )
                knowledge_id = int(cur.lastrowid)
            conn.execute(
                """UPDATE clarification_queue SET status='answered',answer=?,knowledge_item_id=?,
                rescore_required=1,resolved_at=? WHERE id=?""",
                (answer, knowledge_id, now, clarification_id),
            )
            conn.execute(
                """UPDATE evaluations SET status='queued_for_rescore',updated_at=?
                WHERE run_id=? AND session_id=?""",
                (now, row["run_id"], row["session_id"]),
            )
            return knowledge_id

    def ignore_clarification(self, clarification_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE clarification_queue SET status='ignored',rescore_required=0,
                resolved_at=? WHERE id=?""",
                (utc_now(), clarification_id),
            )

    def upsert_agent(self, record: Mapping[str, Any]) -> str:
        agent_id = str(record.get("id") or record.get("staff_id") or uuid.uuid4())
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO agents
                (id,staff_id,canonical_name,canonical_name_en,country,branch,active,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET staff_id=excluded.staff_id,
                canonical_name=excluded.canonical_name,canonical_name_en=excluded.canonical_name_en,
                country=excluded.country,branch=excluded.branch,active=excluded.active,
                updated_at=excluded.updated_at""",
                (
                    agent_id,
                    record.get("staff_id"),
                    record["canonical_name"],
                    record.get("canonical_name_en"),
                    record.get("country"),
                    record.get("branch"),
                    int(record.get("active", True)),
                    now,
                    now,
                ),
            )
        return agent_id

    def list_agents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY active DESC,canonical_name"
            ).fetchall()
        return [dict(row) for row in rows]

    def suggest_alias(
        self,
        alias: str,
        normalized_alias: str,
        *,
        agent_id: str | None,
        confidence: float,
        source: str = "transcript",
    ) -> int:
        with self.transaction() as conn:
            accepted = conn.execute(
                """SELECT id FROM aliases WHERE normalized_alias=? AND status='accepted'
                ORDER BY reviewed_at DESC,id LIMIT 1""",
                (normalized_alias,),
            ).fetchone()
            if accepted:
                return int(accepted["id"])
            row = conn.execute(
                """SELECT id FROM aliases WHERE normalized_alias=? AND agent_id IS ?
                ORDER BY id LIMIT 1""",
                (normalized_alias, agent_id),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE aliases SET alias=?,confidence=?,source=? WHERE id=?",
                    (alias, confidence, source, row["id"]),
                )
                return int(row["id"])
            cur = conn.execute(
                """INSERT INTO aliases
                (alias,normalized_alias,agent_id,confidence,source,status,created_at)
                VALUES (?,?,?,?,?,'suggested',?)""",
                (alias, normalized_alias, agent_id, confidence, source, utc_now()),
            )
            return int(cur.lastrowid)

    def accepted_agent_for_alias(self, normalized_alias: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT agent_id FROM aliases
                WHERE normalized_alias=? AND status='accepted' AND agent_id IS NOT NULL
                ORDER BY reviewed_at DESC,id LIMIT 1""",
                (normalized_alias,),
            ).fetchone()
        return str(row["agent_id"]) if row else None

    def list_aliases(self, status: str | None = None) -> list[dict[str, Any]]:
        where, params = ("", ()) if not status else ("WHERE a.status=?", (status,))
        with self.connect() as conn:
            rows = conn.execute(
                f"""WITH ranked AS (
                    SELECT a.*,g.canonical_name,g.canonical_name_en,g.branch,
                        ROW_NUMBER() OVER (
                            PARTITION BY a.normalized_alias
                            ORDER BY CASE a.status
                                WHEN 'accepted' THEN 0
                                WHEN 'suggested' THEN 1
                                ELSE 2 END,
                                a.reviewed_at DESC,a.id DESC
                        ) alias_rank
                    FROM aliases a LEFT JOIN agents g ON g.id=a.agent_id
                    {where}
                )
                SELECT * FROM ranked WHERE alias_rank=1
                ORDER BY status,confidence DESC,alias""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_accepted_aliases(self) -> int:
        """Apply each authoritative alias mapping to existing conversations."""

        from .roster import normalize_alias

        updated = 0
        with self.transaction() as conn:
            accepted = {
                row["normalized_alias"]: row["agent_id"]
                for row in conn.execute(
                    """SELECT normalized_alias,agent_id FROM aliases
                    WHERE status='accepted' AND agent_id IS NOT NULL"""
                ).fetchall()
            }
            conversations = conn.execute(
                """SELECT session_id,assigned_agent_alias,agent_id FROM conversations
                WHERE assigned_agent_alias IS NOT NULL"""
            ).fetchall()
            now = utc_now()
            for conversation in conversations:
                target = accepted.get(normalize_alias(conversation["assigned_agent_alias"]))
                if target and target != conversation["agent_id"]:
                    conn.execute(
                        "UPDATE conversations SET agent_id=?,updated_at=? WHERE session_id=?",
                        (target, now, conversation["session_id"]),
                    )
                    updated += 1
        return updated

    def review_alias(self, alias_id: int, status: str, agent_id: str | None = None) -> None:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Alias status must be accepted or rejected")
        with self.transaction() as conn:
            current = conn.execute("SELECT * FROM aliases WHERE id=?", (alias_id,)).fetchone()
            if not current:
                raise KeyError(f"Alias not found: {alias_id}")
            now = utc_now()
            if status == "rejected":
                conn.execute(
                    "UPDATE aliases SET status='rejected',reviewed_at=? WHERE id=?",
                    (now, alias_id),
                )
                return
            target_agent = agent_id or current["agent_id"]
            if not target_agent:
                raise ValueError("Select an agent before accepting this alias")
            target = conn.execute(
                """SELECT id FROM aliases
                WHERE normalized_alias=? AND agent_id=? ORDER BY id LIMIT 1""",
                (current["normalized_alias"], target_agent),
            ).fetchone()
            target_id = int(target["id"]) if target else alias_id
            conn.execute(
                """UPDATE aliases SET status='rejected',reviewed_at=?
                WHERE normalized_alias=? AND id<>?""",
                (now, current["normalized_alias"], target_id),
            )
            conn.execute(
                """UPDATE aliases SET status='accepted',agent_id=?,reviewed_at=? WHERE id=?""",
                (target_agent, now, target_id),
            )
        self.reconcile_accepted_aliases()

    def record_spend(self, record: Mapping[str, Any]) -> int:
        now = utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO spend_ledger
                (run_id,session_id,provider,model,request_type,input_tokens,output_tokens,cost_usd,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    record["run_id"],
                    record.get("session_id"),
                    record["provider"],
                    record["model"],
                    record["request_type"],
                    int(record.get("input_tokens", 0)),
                    int(record.get("output_tokens", 0)),
                    float(record.get("cost_usd", 0)),
                    now,
                ),
            )
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) total FROM spend_ledger WHERE run_id=?",
                (record["run_id"],),
            ).fetchone()["total"]
            conn.execute(
                "UPDATE runs SET cost_usd=?,updated_at=? WHERE id=?",
                (total, now, record["run_id"]),
            )
            return int(cur.lastrowid)

    def dashboard(self, run_id: str | None = None) -> dict[str, Any]:
        run = self.get_run(run_id) if run_id else next(iter(self.list_runs(1)), None)
        if not run:
            return {"run": None, "coverage": {}, "agent_results": []}
        with self.connect() as conn:
            statuses = {
                row["status"]: row["count"]
                for row in conn.execute(
                    """SELECT status,COUNT(*) count FROM evaluations
                    WHERE run_id=? GROUP BY status""",
                    (run["id"],),
                ).fetchall()
            }
            agent_rows = conn.execute(
                """SELECT COALESCE(g.canonical_name_en,g.canonical_name,c.assigned_agent_alias,
                    'Unmapped') agent,
                    COUNT(*) processed,
                    SUM(CASE WHEN c.eligible=1 THEN 1 ELSE 0 END) eligible,
                    SUM(CASE WHEN e.status='scored' THEN 1 ELSE 0 END) scored,
                    SUM(CASE WHEN e.status='scored'
                        THEN json_extract(e.scores_json,'$.overall') ELSE 0 END) score_total,
                    AVG(CASE WHEN e.status='scored'
                        THEN json_extract(e.scores_json,'$.overall') END) average_score
                FROM conversations c
                LEFT JOIN evaluations e ON e.run_id=c.run_id AND e.session_id=c.session_id
                LEFT JOIN agents g ON g.id=c.agent_id
                WHERE c.run_id=?
                GROUP BY agent""",
                (run["id"],),
            ).fetchall()
            global_score = conn.execute(
                """SELECT AVG(json_extract(scores_json,'$.overall')) score
                FROM evaluations WHERE run_id=? AND status='scored'""",
                (run["id"],),
            ).fetchone()["score"]
        global_score = float(global_score or 0)
        agent_results = []
        for item in agent_rows:
            row = dict(item)
            scored_count = int(row["scored"] or 0)
            row["average_score"] = (
                round(float(row["average_score"]), 2)
                if row["average_score"] is not None
                else None
            )
            row["ranking_score"] = (
                round(
                    (float(row["score_total"]) + global_score * RANKING_PRIOR_STRENGTH)
                    / (scored_count + RANKING_PRIOR_STRENGTH),
                    2,
                )
                if scored_count
                else None
            )
            row["reliability"] = round(
                scored_count / (scored_count + RANKING_PRIOR_STRENGTH), 4
            )
            row["rank_eligible"] = scored_count >= MIN_RANKED_CONVERSATIONS
            row.pop("score_total", None)
            agent_results.append(row)
        agent_results.sort(
            key=lambda item: (
                bool(item["rank_eligible"]),
                item["ranking_score"] if item["ranking_score"] is not None else -1,
                item["scored"],
            ),
            reverse=True,
        )
        rank = 0
        for row in agent_results:
            if row["rank_eligible"]:
                rank += 1
                row["rank"] = rank
            else:
                row["rank"] = None
        scored = statuses.get("scored", 0)
        unclear = statuses.get("needs_clarification", 0) + statuses.get("queued_for_rescore", 0)
        understood_population = scored + unclear
        total_evaluations = sum(statuses.values())
        coverage = {
            "scored": scored,
            "needs_clarification": unclear,
            "not_scorable": statuses.get("not_scorable", 0),
            "scoring_coverage": scored / understood_population if understood_population else 0,
            "clarification_rate": unclear / understood_population if understood_population else 0,
            "exclusion_rate": statuses.get("not_scorable", 0) / total_evaluations
            if total_evaluations
            else 0,
        }
        return {
            "run": run,
            "coverage": coverage,
            "statuses": statuses,
            "agent_results": agent_results,
            "ranking": {
                "global_average": round(global_score, 2),
                "prior_strength": RANKING_PRIOR_STRENGTH,
                "minimum_scored": MIN_RANKED_CONVERSATIONS,
            },
        }


def import_policy_text(db: Database, path: str | Path, category: str = "company-policy") -> int:
    """Import a UTF-8 policy file as individual numbered items when possible."""

    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if not text:
        return 0
    import re

    matches = list(re.finditer(r"(?m)^\s*(\d+)\s*[-.)]\s*", text))
    if not matches:
        db.add_knowledge(Path(path).stem, text, category=category, source=str(path))
        return 1
    count = 0
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            db.add_knowledge(
                f"Policy {match.group(1)}",
                content,
                category=category,
                source=str(path),
            )
            count += 1
    return count
