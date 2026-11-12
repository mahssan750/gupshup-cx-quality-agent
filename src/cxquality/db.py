"""SQLite persistence for local CX quality runs.

The database intentionally has no column for API keys. Customer transcripts and staff
information stay in the user-selected local database and are never required by the web UI.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RANKING_PRIOR_STRENGTH = 10
MIN_RANKED_CONVERSATIONS = 5

CLARIFICATION_THEMES = {
    "greeting_no_response": (
        "Greeting or no customer response",
        "The chat contains only a greeting, or the customer stopped responding before stating a request.",
    ),
    "media_attachment": (
        "Image, document, or attachment",
        "The missing information is inside media that the text-only workflow deliberately does not read.",
    ),
    "branch_location": (
        "Branch, country, or location",
        "The customer is asking about a branch, nearest location, country, or store address.",
    ),
    "product_information": (
        "Product details or availability",
        "The customer asks what a product is, whether it is available, or how it should be used.",
    ),
    "health_guidance": (
        "Health, dosage, or suitability",
        "The question needs an approved rule for medical, dosage, test, pregnancy, or suitability guidance.",
    ),
    "order_delivery": (
        "Order, delivery, return, or refund",
        "The conversation concerns order status, delivery, cancellation, exchange, return, or refund.",
    ),
    "price_payment": (
        "Price, discount, or payment",
        "The conversation concerns price, promotions, shipping cost, tax, checkout, or payment failure.",
    ),
    "unclear_reference": (
        "Unclear reference or missing earlier context",
        "Words such as “this” or “it” cannot be understood from the visible text alone.",
    ),
    "unresolved_closure": (
        "Unanswered request or chat closure",
        "The customer request may have been left unanswered or the chat may have closed too early.",
    ),
    "other": (
        "Other business rule",
        "Questions that do not yet fit one of the recurring patterns above.",
    ),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalized_question(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _clarification_theme(question: str, context: str, evidence: str) -> str:
    text = unicodedata.normalize("NFKC", f"{question} {context} {evidence}").casefold()

    def contains(*terms: str) -> bool:
        return any(term in text for term in terms)

    if contains(
        "image", "document", "attachment", "photo", "media", "صورة", "الصورة", "مرفق", "ملف"
    ):
        return "media_attachment"
    if contains(
        "greeting", "only a greeting", "did not respond", "didn't respond", "no customer request",
        "customer does not respond", "customer stopped responding", "تحية", "لم يرد", "لم يرد العميل",
    ):
        return "greeting_no_response"
    if contains(
        "branch", "nearest", "location", "which country", "store address", "country are you in",
        "فرع", "فروع", "اقرب", "أقرب", "الموقع", "موقع", "الدولة", "عنوان"
    ):
        return "branch_location"
    if contains(
        "pregnan", "dosage", "dose", "doctor", "medical", "suitable", "side effect", "blood test",
        "vitamin test", "health", "جرعة", "حامل", "الحمل", "طبيب", "دكتور", "فحص", "تحاليل", "مناسب"
    ):
        return "health_guidance"
    if contains(
        "delivery", "order", "tracking", "refund", "return", "exchange", "cancel", "shipment",
        "توصيل", "طلب", "الطلب", "شحن", "استرجاع", "استبدال", "الغاء", "إلغاء"
    ):
        return "order_delivery"
    if contains(
        "price", "discount", "payment", "checkout", "shipping cost", "tax", "offer", "promotion",
        "سعر", "خصم", "دفع", "تكلفة", "ضريبة", "عرض", "عروض"
    ):
        return "price_payment"
    if contains(
        "product", "available", "availability", "ingredient", "how to use", "more information",
        "more info", "تفاصيل المنتج", "المنتج", "متوفر", "متاح", "المكونات", "طريقة الاستخدام"
    ):
        return "product_information"
    if contains(
        "referring to", "what did the customer mean", "what was the customer asking", "what did",
        "original request", "earlier context", "by this", "by it", "يقصد", "المقصود", "ماذا طلب"
    ):
        return "unclear_reference"
    if contains(
        "resolved", "closed", "closure", "unanswered", "no response was provided", "before resolving",
        "before the chat was closed", "تم إغلاق", "اغلاق", "إغلاق", "بدون رد", "لم يتم الرد"
    ):
        return "unresolved_closure"
    return "other"


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
    decision_json TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0 CHECK(cost_usd >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(session_id) REFERENCES conversations(session_id),
    UNIQUE(run_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_evaluations_run_status ON evaluations(run_id, status);

CREATE TABLE IF NOT EXISTS manual_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    review_kind TEXT NOT NULL CHECK(review_kind IN ('mistake','quality')),
    component TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('confirmed','corrected','dismissed')),
    ai_value TEXT,
    manager_value TEXT,
    original_scores_json TEXT NOT NULL DEFAULT '{}',
    manager_reason TEXT NOT NULL,
    knowledge_rule TEXT NOT NULL,
    knowledge_item_id INTEGER,
    reviewer TEXT NOT NULL DEFAULT 'Quality Manager',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(session_id) REFERENCES conversations(session_id),
    FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id),
    UNIQUE(run_id, session_id, review_kind, component)
);

CREATE INDEX IF NOT EXISTS idx_manual_reviews_kind ON manual_reviews(review_kind, decision);

CREATE TABLE IF NOT EXISTS manual_review_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_review_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    manager_value TEXT,
    manager_reason TEXT NOT NULL,
    knowledge_rule TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(manual_review_id) REFERENCES manual_reviews(id)
);

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
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(evaluations)").fetchall()
            }
            if "decision_json" not in columns:
                conn.execute(
                    "ALTER TABLE evaluations ADD COLUMN decision_json TEXT NOT NULL DEFAULT '{}'"
                )

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
        if not zip_folder.strip():
            raise ValueError("zip_folder is required")
        if stop_at_usd > budget_usd:
            raise ValueError("stop_at_usd cannot exceed budget_usd")
        if budget_usd <= 0 or stop_at_usd <= 0:
            raise ValueError("Budget limits must be greater than zero")
        if sample_size is not None and sample_size < 1:
            raise ValueError("sample_size must be at least 1")
        try:
            if datetime.fromisoformat(from_date) > datetime.fromisoformat(to_date):
                raise ValueError("from_date cannot be later than to_date")
        except (TypeError, ValueError) as exc:
            if str(exc) == "from_date cannot be later than to_date":
                raise
            raise ValueError("Run dates must use YYYY-MM-DD") from exc
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

    def active_run_for_folder(self, zip_folder: str | Path) -> dict[str, Any] | None:
        resolved = str(Path(zip_folder).expanduser().resolve())
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM runs
                WHERE zip_folder = ? AND status IN ('queued','running','blocked','stopped_budget')
                ORDER BY created_at DESC LIMIT 1""",
                (resolved,),
            ).fetchone()
        return dict(row) if row else None

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

    def update_run_controls(
        self,
        run_id: str,
        *,
        budget_usd: float,
        stop_at_usd: float,
        roster_path: str | None,
        settings: Mapping[str, Any],
    ) -> None:
        if budget_usd <= 0 or stop_at_usd <= 0 or stop_at_usd > budget_usd:
            raise ValueError("Stop threshold must be positive and no higher than the hard budget")
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run["status"] == "running":
            raise ValueError("Stop the run before changing its controls")
        with self.transaction() as conn:
            conn.execute(
                """UPDATE runs SET budget_usd = ?, stop_at_usd = ?, roster_path = ?,
                updated_at = ? WHERE id = ?""",
                (budget_usd, stop_at_usd, roster_path, utc_now(), run_id),
            )
        self.save_checkpoint(run_id, {"settings": dict(settings)})

    def request_run_stop(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run["status"] not in {"queued", "running"}:
            raise ValueError("Only a queued or running run can be stopped")
        self.update_run(
            run_id,
            status="blocked",
            error="USER_STOP: Stop requested. Finishing only the already-started AI requests.",
        )

    def user_stop_requested(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        return bool(
            run
            and run["status"] == "blocked"
            and str(run.get("error") or "").startswith("USER_STOP:")
        )

    def save_checkpoint(self, run_id: str, checkpoint: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT checkpoint_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            merged = json.loads(row["checkpoint_json"] or "{}")
            merged.update(checkpoint)
            conn.execute(
                "UPDATE runs SET checkpoint_json = ?, updated_at = ? WHERE id = ?",
                (_json(merged), utc_now(), run_id),
            )

    def checkpoint(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        return json.loads(run["checkpoint_json"] or "{}")

    def control_summary(self) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT
                    (SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')) active_runs,
                    (SELECT COUNT(*) FROM runs) total_runs,
                    (SELECT COUNT(*) FROM knowledge_items WHERE active = 1) active_knowledge,
                    (SELECT COUNT(*) FROM clarification_queue WHERE status = 'pending') pending_questions,
                    (SELECT COUNT(*) FROM aliases WHERE status = 'suggested') pending_aliases
                """
            ).fetchone()
        return dict(row)

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
                    clarification_question,decision_json,cost_usd,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,session_id) DO UPDATE SET
                    status=excluded.status,model=excluded.model,scores_json=excluded.scores_json,
                    evidence_json=excluded.evidence_json,confidence=excluded.confidence,
                    clarification_question=excluded.clarification_question,
                    decision_json=excluded.decision_json,
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
                    _json(record.get("decision", {})),
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
                f"""WITH grouped AS (
                    SELECT q.*,
                        COUNT(*) OVER (
                            PARTITION BY q.status,
                            CASE WHEN q.status='answered' AND q.knowledge_item_id IS NOT NULL
                                THEN 'knowledge:' || q.knowledge_item_id
                                ELSE 'question:' || q.question END
                        ) similar_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY q.status,
                            CASE WHEN q.status='answered' AND q.knowledge_item_id IS NOT NULL
                                THEN 'knowledge:' || q.knowledge_item_id
                                ELSE 'question:' || q.question END
                            ORDER BY q.created_at DESC,q.id DESC
                        ) question_rank
                    FROM clarification_queue q {where}
                )
                SELECT q.*,c.assigned_agent_alias,c.language,k.title knowledge_title
                FROM grouped q JOIN conversations c ON c.session_id=q.session_id
                LEFT JOIN knowledge_items k ON k.id=q.knowledge_item_id
                WHERE q.question_rank=1 ORDER BY q.created_at DESC""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _clarification_rows(self, status: str = "pending") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT q.*,c.assigned_agent_alias,c.language,c.metrics_json,
                    e.evidence_json,g.canonical_name_en,g.canonical_name,g.branch,g.staff_id
                FROM clarification_queue q
                JOIN conversations c ON c.session_id=q.session_id
                LEFT JOIN evaluations e ON e.run_id=q.run_id AND e.session_id=q.session_id
                LEFT JOIN agents g ON g.id=c.agent_id
                WHERE q.status=? ORDER BY q.created_at DESC,q.id DESC""",
                (status,),
            ).fetchall()
        output = []
        for raw in rows:
            row = dict(raw)
            try:
                evidence = json.loads(row.pop("evidence_json") or "[]")
            except json.JSONDecodeError:
                evidence = []
            try:
                metrics = json.loads(row.pop("metrics_json") or "{}")
            except json.JSONDecodeError:
                metrics = {}
            row["evidence"] = [str(item) for item in evidence if str(item).strip()]
            row["metrics"] = metrics
            row["agent"] = (
                row.get("canonical_name_en")
                or row.get("canonical_name")
                or row.get("assigned_agent_alias")
                or "Unmapped"
            )
            row["theme"] = _clarification_theme(
                row.get("question") or "",
                row.get("context_summary") or "",
                " ".join(row["evidence"]),
            )
            output.append(row)
        return output

    def list_clarification_groups(
        self, status: str = "pending", *, examples_per_group: int = 4
    ) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in self._clarification_rows(status):
            theme = row["theme"]
            label, description = CLARIFICATION_THEMES[theme]
            group = groups.setdefault(
                theme,
                {
                    "key": theme,
                    "label": label,
                    "description": description,
                    "count": 0,
                    "questions": [],
                    "examples": [],
                },
            )
            group["count"] += 1
            if row["question"] not in group["questions"] and len(group["questions"]) < 4:
                group["questions"].append(row["question"])
            if len(group["examples"]) < examples_per_group:
                group["examples"].append(row)
        return sorted(groups.values(), key=lambda item: (-item["count"], item["label"]))

    def answer_clarification_group(self, theme: str, answer: str) -> tuple[int, int]:
        if theme not in CLARIFICATION_THEMES:
            raise KeyError(theme)
        answer = answer.strip()
        if not answer:
            raise ValueError("An answer is required")
        rows = [row for row in self._clarification_rows("pending") if row["theme"] == theme]
        if not rows:
            raise ValueError("This clarification group has no pending conversations")
        label, description = CLARIFICATION_THEMES[theme]
        representative = list(dict.fromkeys(row["question"] for row in rows))[:5]
        now = utc_now()
        content = (
            f"Manager-approved pattern\nTheme: {label}\nMeaning: {description}\n"
            + "Representative questions:\n- "
            + "\n- ".join(representative)
            + f"\nApproved answer: {answer}"
        )
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO knowledge_items
                (title,content,category,source,active,created_at,updated_at)
                VALUES (?,?,?,?,1,?,?)""",
                (f"Clarification pattern: {label}", content, "clarification", "grouped-inbox", now, now),
            )
            knowledge_id = int(cur.lastrowid)
            for row in rows:
                changed = conn.execute(
                    """UPDATE clarification_queue SET status='answered',answer=?,knowledge_item_id=?,
                    rescore_required=1,resolved_at=? WHERE id=? AND status='pending'""",
                    (answer, knowledge_id, now, row["id"]),
                ).rowcount
                if changed:
                    conn.execute(
                        """UPDATE evaluations SET status='queued_for_rescore',updated_at=?
                        WHERE run_id=? AND session_id=?""",
                        (now, row["run_id"], row["session_id"]),
                    )
        return knowledge_id, len(rows)

    def ignore_clarification_group(self, theme: str) -> int:
        if theme not in CLARIFICATION_THEMES:
            raise KeyError(theme)
        rows = [row for row in self._clarification_rows("pending") if row["theme"] == theme]
        now = utc_now()
        with self.transaction() as conn:
            for row in rows:
                conn.execute(
                    """UPDATE clarification_queue SET status='ignored',rescore_required=0,
                    resolved_at=? WHERE id=? AND status='pending'""",
                    (now, row["id"]),
                )
        return len(rows)

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
                learned_content = (
                    f"Manager-approved pattern\nQuestion: {row['question']}\nApproved answer: {answer}"
                )
                cur = conn.execute(
                    """INSERT INTO knowledge_items
                    (title,content,category,source,active,created_at,updated_at)
                    VALUES (?,?,?,?,1,?,?)""",
                    (
                        f"Clarification: {row['question'][:100]}",
                        learned_content,
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
            if knowledge_id is not None:
                pending = conn.execute(
                    """SELECT id,run_id,session_id,question FROM clarification_queue
                    WHERE status='pending' AND id<>?""",
                    (clarification_id,),
                ).fetchall()
                for candidate in pending:
                    if _normalized_question(row["question"]) != _normalized_question(
                        candidate["question"]
                    ):
                        continue
                    conn.execute(
                        """UPDATE clarification_queue SET status='answered',answer=?,
                        knowledge_item_id=?,rescore_required=1,resolved_at=? WHERE id=?""",
                        (answer, knowledge_id, now, candidate["id"]),
                    )
                    conn.execute(
                        """UPDATE evaluations SET status='queued_for_rescore',updated_at=?
                        WHERE run_id=? AND session_id=?""",
                        (now, candidate["run_id"], candidate["session_id"]),
                    )
            return knowledge_id

    def clarification_learning_count(self, knowledge_id: int) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM clarification_queue WHERE knowledge_item_id=?",
                    (knowledge_id,),
                ).fetchone()[0]
            )

    def revise_clarification_answer(self, clarification_id: int, answer: str) -> int:
        """Update one learned pattern and queue all linked conversations for future re-scoring."""

        answer = answer.strip()
        if not answer:
            raise ValueError("An updated answer is required")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                """SELECT q.*,k.content knowledge_content FROM clarification_queue q
                LEFT JOIN knowledge_items k ON k.id=q.knowledge_item_id WHERE q.id=?""",
                (clarification_id,),
            ).fetchone()
            if not row:
                raise KeyError(clarification_id)
            if row["status"] != "answered" or not row["knowledge_item_id"]:
                raise ValueError("Only an answered clarification with Knowledge can be revised")
            content = row["knowledge_content"] or "Manager-approved pattern"
            if re.search(r"(?s)\nApproved answer:.*$", content):
                content = re.sub(
                    r"(?s)\nApproved answer:.*$", f"\nApproved answer: {answer}", content
                )
            else:
                content = f"{content.rstrip()}\nApproved answer: {answer}"
            conn.execute(
                """UPDATE knowledge_items SET content=?,active=1,updated_at=? WHERE id=?""",
                (content, now, row["knowledge_item_id"]),
            )
            linked = conn.execute(
                """SELECT id,run_id,session_id FROM clarification_queue
                WHERE knowledge_item_id=?""",
                (row["knowledge_item_id"],),
            ).fetchall()
            for item in linked:
                conn.execute(
                    """UPDATE clarification_queue SET status='answered',answer=?,rescore_required=1,
                    resolved_at=? WHERE id=?""",
                    (answer, now, item["id"]),
                )
                conn.execute(
                    """UPDATE evaluations SET status='queued_for_rescore',updated_at=?
                    WHERE run_id=? AND session_id=?""",
                    (now, item["run_id"], item["session_id"]),
                )
            return len(linked)

    def propagate_answered_patterns(self) -> int:
        """Upgrade existing answers and apply high-confidence matches to pending questions."""

        updated = 0
        with self.transaction() as conn:
            answered = conn.execute(
                """SELECT q.*,k.content knowledge_content FROM clarification_queue q
                JOIN knowledge_items k ON k.id=q.knowledge_item_id
                WHERE q.status='answered' AND q.knowledge_item_id IS NOT NULL"""
            ).fetchall()
            pending = conn.execute(
                "SELECT id,run_id,session_id,question FROM clarification_queue WHERE status='pending'"
            ).fetchall()
            now = utc_now()
            for pattern in answered:
                learned_content = (
                    "Manager-approved pattern\n"
                    f"Question: {pattern['question']}\nApproved answer: {pattern['answer']}"
                )
                if pattern["knowledge_content"] != learned_content:
                    conn.execute(
                        "UPDATE knowledge_items SET content=?,updated_at=? WHERE id=?",
                        (learned_content, now, pattern["knowledge_item_id"]),
                    )
                for candidate in pending:
                    if _normalized_question(pattern["question"]) != _normalized_question(
                        candidate["question"]
                    ):
                        continue
                    changed = conn.execute(
                        """UPDATE clarification_queue SET status='answered',answer=?,
                        knowledge_item_id=?,rescore_required=1,resolved_at=?
                        WHERE id=? AND status='pending'""",
                        (
                            pattern["answer"],
                            pattern["knowledge_item_id"],
                            now,
                            candidate["id"],
                        ),
                    ).rowcount
                    if not changed:
                        continue
                    conn.execute(
                        """UPDATE evaluations SET status='queued_for_rescore',updated_at=?
                        WHERE run_id=? AND session_id=?""",
                        (now, candidate["run_id"], candidate["session_id"]),
                    )
                    updated += 1
        return updated

    def rescore_session_ids(self, run_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT session_id FROM evaluations
                WHERE run_id=? AND status='queued_for_rescore'""",
                (run_id,),
            ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def evaluation_status(self, run_id: str, session_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM evaluations WHERE run_id=? AND session_id=?",
                (run_id, session_id),
            ).fetchone()
        return str(row["status"]) if row else None

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
                    SELECT a.*,g.canonical_name,g.canonical_name_en,g.branch,g.staff_id,
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

    def scored_review_rows(
        self, *, run_id: str | None = None, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["e.status='scored'"]
        params: list[Any] = []
        if run_id:
            clauses.append("e.run_id=?")
            params.append(run_id)
        if agent_id:
            clauses.append("c.agent_id=?")
            params.append(agent_id)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT e.id evaluation_id,e.run_id,e.session_id,e.model,e.confidence,
                    e.scores_json,e.evidence_json,e.decision_json,e.updated_at,
                    c.source_zip,c.started_at,c.ended_at,c.assigned_agent_alias,c.agent_id,
                    COALESCE(a.canonical_name_en,a.canonical_name,c.assigned_agent_alias,'Unmapped')
                        agent_name,
                    a.branch,a.staff_id,
                    mr.id review_id,mr.decision review_decision,mr.manager_value,
                    mr.manager_reason,mr.knowledge_rule,mr.knowledge_item_id,mr.reviewer,
                    mr.original_scores_json,
                    mr.updated_at review_updated_at
                FROM evaluations e
                JOIN conversations c ON c.session_id=e.session_id
                LEFT JOIN agents a ON a.id=c.agent_id
                LEFT JOIN manual_reviews mr ON mr.run_id=e.run_id
                    AND mr.session_id=e.session_id AND mr.review_kind='quality'
                    AND mr.component='overall'
                WHERE {' AND '.join(clauses)}
                ORDER BY e.updated_at DESC,e.session_id""",
                params,
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["scores"] = json.loads(item.pop("scores_json") or "{}")
            original_scores_json = item.pop("original_scores_json")
            item["original_scores"] = (
                json.loads(original_scores_json) if original_scores_json else dict(item["scores"])
            )
            item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
            item["ai_decision"] = json.loads(item.pop("decision_json") or "{}")
            output.append(item)
        return output

    def scored_review_item(self, run_id: str, session_id: str) -> dict[str, Any] | None:
        rows = self.scored_review_rows(run_id=run_id)
        return next((row for row in rows if str(row["session_id"]) == str(session_id)), None)

    def manual_review_map(self, review_kind: str) -> dict[tuple[str, str, str], dict[str, Any]]:
        if review_kind not in {"mistake", "quality"}:
            raise ValueError("Unknown review kind")
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM manual_reviews WHERE review_kind=?", (review_kind,)
            ).fetchall()
        return {
            (str(row["run_id"]), str(row["session_id"]), str(row["component"])): dict(row)
            for row in rows
        }

    def manual_review_history(self, review_id: int | None) -> list[dict[str, Any]]:
        if not review_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM manual_review_history WHERE manual_review_id=?
                ORDER BY created_at DESC,id DESC""",
                (review_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def apply_manual_review(
        self,
        *,
        run_id: str,
        session_id: str,
        review_kind: str,
        component: str,
        decision: str,
        manager_reason: str,
        knowledge_rule: str,
        reviewer: str = "Quality Manager",
        corrected_scores: Mapping[str, object] | None = None,
        corrected_value: float | None = None,
        corrected_overall: float | None = None,
    ) -> int:
        from .quality_review import COMPONENTS, normalized_component_scores, recompute_overall

        if review_kind not in {"mistake", "quality"}:
            raise ValueError("Review kind must be mistake or quality")
        if decision not in {"confirmed", "corrected", "dismissed"}:
            raise ValueError("Choose confirm, correct, or dismiss")
        if review_kind == "quality":
            component = "overall"
        elif component not in COMPONENTS:
            raise ValueError("Unknown quality component")
        manager_reason = manager_reason.strip()
        knowledge_rule = knowledge_rule.strip()
        reviewer = reviewer.strip() or "Quality Manager"
        if not manager_reason:
            raise ValueError("A manager reason is required")
        if not knowledge_rule:
            raise ValueError("A reusable Knowledge rule is required")

        now = utc_now()
        with self.transaction() as conn:
            evaluation = conn.execute(
                """SELECT e.scores_json,e.decision_json,e.status,
                    COALESCE(a.canonical_name_en,a.canonical_name,c.assigned_agent_alias,'Unmapped')
                        agent_name,
                    a.branch
                FROM evaluations e
                JOIN conversations c ON c.session_id=e.session_id
                LEFT JOIN agents a ON a.id=c.agent_id
                WHERE e.run_id=? AND e.session_id=?""",
                (run_id, session_id),
            ).fetchone()
            if not evaluation or evaluation["status"] != "scored":
                raise KeyError("Scored conversation was not found")
            current_scores = json.loads(evaluation["scores_json"] or "{}")
            existing = conn.execute(
                """SELECT * FROM manual_reviews
                WHERE run_id=? AND session_id=? AND review_kind=? AND component=?""",
                (run_id, session_id, review_kind, component),
            ).fetchone()
            prior_review = conn.execute(
                """SELECT original_scores_json FROM manual_reviews
                WHERE run_id=? AND session_id=? ORDER BY id LIMIT 1""",
                (run_id, session_id),
            ).fetchone()
            original_scores = (
                json.loads(existing["original_scores_json"] or "{}")
                if existing
                else json.loads(prior_review["original_scores_json"] or "{}")
                if prior_review
                else dict(current_scores)
            )
            ai_value = (
                existing["ai_value"]
                if existing
                else str(original_scores.get(component, original_scores.get("overall", "")))
            )
            resulting_scores = dict(current_scores)
            manager_value: str
            if decision in {"corrected", "dismissed"}:
                if review_kind == "quality":
                    if corrected_overall is not None:
                        overall = float(corrected_overall)
                        if overall < 0 or overall > 100:
                            raise ValueError("Quality score must be between 0 and 100")
                        resulting_scores["overall"] = round(overall, 2)
                        manager_value = str(round(overall, 2))
                    else:
                        resulting_scores = normalized_component_scores(corrected_scores or {})
                        manager_value = str(resulting_scores["overall"])
                else:
                    if corrected_overall is not None:
                        overall = float(corrected_overall)
                        if overall < 0 or overall > 100:
                            raise ValueError("Quality score must be between 0 and 100")
                        resulting_scores["overall"] = round(overall, 2)
                        manager_value = str(round(overall, 2))
                    else:
                        if corrected_value is None:
                            raise ValueError("A corrected quality score is required")
                        value = float(corrected_value)
                        if value < 0 or value > 4:
                            raise ValueError("Corrected score must be between 0 and 4")
                        resulting_scores[component] = round(value, 3)
                        resulting_scores["overall"] = recompute_overall(resulting_scores)
                        manager_value = str(round(value, 3))
                conn.execute(
                    """UPDATE evaluations SET scores_json=?,confidence=1,updated_at=?
                    WHERE run_id=? AND session_id=?""",
                    (_json(resulting_scores), now, run_id, session_id),
                )
            else:
                manager_value = str(current_scores.get(component, current_scores.get("overall", "")))

            knowledge_title = (
                f"Manager QA rule — {component.replace('_', ' ').title()}"
                if review_kind == "mistake"
                else "Manager QA rule — Conversation scoring"
            )
            knowledge_content = (
                "Manager-approved reusable quality rule:\n"
                f"{knowledge_rule}\n"
                "Use this rule when the same service pattern appears in future conversations."
            )
            knowledge_id = int(existing["knowledge_item_id"]) if existing and existing["knowledge_item_id"] else None
            if knowledge_id:
                conn.execute(
                    """UPDATE knowledge_items SET title=?,content=?,category='quality-review',
                    source='manager-review',active=1,updated_at=? WHERE id=?""",
                    (knowledge_title, knowledge_content, now, knowledge_id),
                )
            else:
                knowledge_id = int(
                    conn.execute(
                        """INSERT INTO knowledge_items
                        (title,content,category,source,active,created_at,updated_at)
                        VALUES (?,?,'quality-review','manager-review',1,?,?)""",
                        (knowledge_title, knowledge_content, now, now),
                    ).lastrowid
                )

            decision_payload = json.loads(evaluation["decision_json"] or "{}")
            decision_payload["manual_override"] = {
                "review_kind": review_kind,
                "component": component,
                "decision": decision,
                "manager_reason": manager_reason,
                "reviewer": reviewer,
                "updated_at": now,
            }
            conn.execute(
                """UPDATE evaluations SET decision_json=?,updated_at=?
                WHERE run_id=? AND session_id=?""",
                (_json(decision_payload), now, run_id, session_id),
            )
            conn.execute(
                """INSERT INTO manual_reviews
                (run_id,session_id,review_kind,component,decision,ai_value,manager_value,
                original_scores_json,manager_reason,knowledge_rule,knowledge_item_id,reviewer,
                created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,session_id,review_kind,component) DO UPDATE SET
                    decision=excluded.decision,manager_value=excluded.manager_value,
                    manager_reason=excluded.manager_reason,knowledge_rule=excluded.knowledge_rule,
                    knowledge_item_id=excluded.knowledge_item_id,reviewer=excluded.reviewer,
                    updated_at=excluded.updated_at""",
                (
                    run_id,
                    session_id,
                    review_kind,
                    component,
                    decision,
                    ai_value,
                    manager_value,
                    _json(original_scores),
                    manager_reason,
                    knowledge_rule,
                    knowledge_id,
                    reviewer,
                    now,
                    now,
                ),
            )
            review_id = int(
                conn.execute(
                    """SELECT id FROM manual_reviews
                    WHERE run_id=? AND session_id=? AND review_kind=? AND component=?""",
                    (run_id, session_id, review_kind, component),
                ).fetchone()["id"]
            )
            conn.execute(
                """INSERT INTO manual_review_history
                (manual_review_id,decision,manager_value,manager_reason,knowledge_rule,reviewer,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    review_id,
                    decision,
                    manager_value,
                    manager_reason,
                    knowledge_rule,
                    reviewer,
                    now,
                ),
            )
        return review_id

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
                    g.branch branch,
                    COALESCE(g.staff_id,g.id) staff_id,
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
                GROUP BY agent,g.branch,g.staff_id,g.id""",
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
                item["scored"],
                item["ranking_score"] if item["ranking_score"] is not None else -1,
                item["reliability"],
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
