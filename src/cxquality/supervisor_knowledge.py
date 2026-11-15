"""Import WhatsApp supervisor feedback into a reviewable local knowledge queue."""

from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .db import Database, utc_now

MESSAGE_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{4}),\s+(?P<time>.+?)\s+-\s+(?P<body>.*)$"
)
ATTACHED_RE = re.compile(
    r"(?P<file>[^\r\n]+?\.(?:jpe?g|png|webp))\s+\(file attached\)", re.IGNORECASE
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MENTION_RE = re.compile("@?[^\\n]*" + chr(0x2068) + ".*?" + chr(0x2069))
FEEDBACK_RE = re.compile(
    r"خصم|نقط[ةه]|نقاط|يجب|كان يجب|المفروض|الأفضل|الافضل|الرد صحيح|خط[أا]|"
    r"ملاحظة|تنبيه|لم يتم|عدم|يرجى|توضيح|توجيه|ترشيح|الرد عل|حساب السعر|"
    r"ارسال|إرسال|اخبار العميل|إخبار العميل|الحصول عل[ىي] بيانات|العميل بالفعل|"
    r"لا حاجة|لا حاجه|لا تختلف|غير شامل|متوفر عندنا|العروض|الفروع",
    re.IGNORECASE,
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
GROUP_PENALTY_RE = re.compile(
    r"خصم(?:\s+\d+\s*(?:نقط(?:ة|ه)|نقاط))?\s+عل[ىي]\s+الجميع",
    re.IGNORECASE,
)
PENALTY_RE = re.compile(r"خصم\s+\d+\s*(?:نقط(?:ة|ه)|نقاط)", re.IGNORECASE)
RULE_CUE_RE = re.compile(
    r"(?:لا\s+يجب|يجب|يرج[ىي]|التأكد|التاكيد|تم\s+التنبيه|لماذا|اين|أين|"
    r"العميل|العميلة|حاليا|حالياً|لا\s+يوجد|متوفر\s+لدينا|في\s+هذه\s+الحالة|"
    r"لعدم|عدم|ممكن|تم\s+ارسال|تم\s+إرسال)",
    re.IGNORECASE,
)
PURE_PENALTY_RE = re.compile(
    r"^(?:عل[ىي]\s+)?(?:الشيفت|الوردية)(?:\s+المسائي|\s+الصباحي)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WhatsAppMessage:
    index: int
    date: str
    time: str
    sender: str | None
    content: str


def parse_whatsapp_export(text: str) -> list[WhatsAppMessage]:
    """Parse multiline WhatsApp exports without treating caption lines as new messages."""
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        match = MESSAGE_RE.match(raw_line)
        if match:
            if current:
                rows.append(current)
            body = match.group("body")
            sender, separator, content = body.partition(": ")
            current = {
                "date": match.group("date"),
                "time": match.group("time"),
                "sender": sender.strip() if separator else None,
                "content": content if separator else body,
            }
        elif current is not None:
            current["content"] += "\n" + raw_line
    if current:
        rows.append(current)
    return [WhatsAppMessage(index=index, **row) for index, row in enumerate(rows)]


def _archive_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _caption(content: str) -> tuple[str | None, str]:
    match = ATTACHED_RE.search(content)
    filename = PurePosixPath(match.group("file")).name if match else None
    value = ATTACHED_RE.sub("", content).replace("<Media omitted>", "").strip()
    return filename, value


def _meaningful_text(caption: str) -> str:
    value = URL_RE.sub("", caption)
    value = MENTION_RE.sub("", value)
    return " ".join(value.split()).strip(" -|/")


def is_group_penalty(caption: str) -> bool:
    """Return True for supervisor broadcasts that penalize everyone."""
    return bool(GROUP_PENALTY_RE.search(unicodedata.normalize("NFKC", caption)))


def supervisor_rule_text(caption: str) -> str:
    """Remove case-only noise while preserving the supervisor's written judgment."""
    value = unicodedata.normalize("NFKC", caption)
    value = URL_RE.sub(" ", value)
    value = MENTION_RE.sub(" ", value)
    value = PENALTY_RE.sub(" ", value)
    value = re.sub(r"^\s*(?:نفس\s+الأمر|نفس\s+الامر|نفس)\s+", "", value, flags=re.IGNORECASE)
    value = " ".join(value.split()).strip(" -|/،؛:.")
    cue = RULE_CUE_RE.search(value)
    if cue and cue.start() > 0 and len(value[:cue.start()].split()) <= 5:
        value = value[cue.start():]
    return " ".join(value.split()).strip(" -|/،؛:.")


def _rule_key(rule: str) -> str:
    value = unicodedata.normalize("NFKC", rule).casefold()
    value = re.sub("[أإآ]", "ا", value)
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def approve_imported_supervisor_rules(
    database: Database,
    *,
    reviewer: str = "Codex — manager bulk instruction",
    dry_run: bool = False,
) -> dict[str, int]:
    """Approve written supervisor judgments and exclude non-rules deterministically.

    One active Knowledge item is created per distinct cleaned rule. All matching source
    cases remain linked to it, so the audit trail is preserved without retrieval bias
    from duplicate rules.
    """
    with database.connect() as conn:
        cases = conn.execute(
            """SELECT c.*,i.supervisor_sender FROM supervisor_cases c
            JOIN supervisor_imports i ON i.id=c.import_id
            WHERE c.sender=i.supervisor_sender ORDER BY c.id"""
        ).fetchall()
        existing = conn.execute(
            """SELECT id,source FROM knowledge_items
            WHERE source LIKE 'supervisor-bulk:%'"""
        ).fetchall()

    actions: list[tuple[str, object, str]] = []
    counts = Counter(examined=len(cases))
    for case in cases:
        caption = str(case["caption_original"] or "").strip()
        if is_group_penalty(caption):
            actions.append(("group_penalty", case, ""))
            counts["group_penalty_excluded"] += 1
            continue
        rule = supervisor_rule_text(caption)
        if not rule or PURE_PENALTY_RE.fullmatch(rule):
            actions.append(("non_rule", case, rule))
            counts["non_rule_excluded"] += 1
            continue
        actions.append(("approve", case, rule))
        counts["approved_cases"] += 1

    unique_rules = {_rule_key(rule) for action, _, rule in actions if action == "approve"}
    counts["distinct_rules"] = len(unique_rules)
    counts["deduplicated_cases"] = counts["approved_cases"] - counts["distinct_rules"]
    if dry_run:
        return dict(counts)

    now = utc_now()
    source_to_id = {str(row["source"]): int(row["id"]) for row in existing}
    key_to_id: dict[str, int] = {}
    with database.transaction() as conn:
        for action, case, rule in actions:
            case_id = int(case["id"])
            old_knowledge_id = case["knowledge_item_id"]
            if action != "approve":
                if old_knowledge_id:
                    conn.execute(
                        "UPDATE knowledge_items SET active=0,updated_at=? WHERE id=?",
                        (now, int(old_knowledge_id)),
                    )
                reason = (
                    "Excluded by manager instruction: group-wide penalty messages are not rules."
                    if action == "group_penalty"
                    else "Excluded automatically: no written supervisor rule remained after cleanup."
                )
                conn.execute(
                    """UPDATE supervisor_cases SET review_status='excluded',
                    final_category='unrelated',caption_corrected=?,rule_text='',review_notes=?,
                    reviewer=?,knowledge_item_id=NULL,updated_at=? WHERE id=?""",
                    (rule, reason, reviewer, now, case_id),
                )
                continue

            key = _rule_key(rule)
            fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            source = f"supervisor-bulk:{fingerprint}"
            knowledge_id = key_to_id.get(key) or source_to_id.get(source)
            content = (
                "قاعدة شركة مستخلصة من حكم مشرف معتمد.\n"
                f"توجيه المشرف: {rule}\n"
                "تُطبّق فقط عندما يتطابق سياق العميل والدولة والمنتج والقناة والشروط "
                "الظاهرة في المحادثة. اسم الموظف ودرجة الخصم ليسا جزءاً من القاعدة."
            )
            if knowledge_id:
                conn.execute(
                    """UPDATE knowledge_items SET content=?,category='company-supervisor-rule',
                    active=1,updated_at=? WHERE id=?""",
                    (content, now, knowledge_id),
                )
                counts["reused_rules"] += 1
            else:
                knowledge_id = int(
                    conn.execute(
                        """INSERT INTO knowledge_items
                        (title,content,category,source,active,created_at,updated_at)
                        VALUES (?,?,'company-supervisor-rule',?,1,?,?)""",
                        (f"قاعدة مشرف معتمدة — SUP-{case_id:06d}", content, source, now, now),
                    ).lastrowid
                )
                source_to_id[source] = knowledge_id
                counts["created_rules"] += 1
            key_to_id[key] = knowledge_id
            conn.execute(
                """UPDATE supervisor_cases SET review_status='approved',
                final_category='rule_candidate',caption_corrected=?,rule_text=?,review_notes=?,
                reviewer=?,knowledge_item_id=?,updated_at=? WHERE id=?""",
                (
                    rule,
                    rule,
                    "Approved and promoted in bulk by explicit manager instruction.",
                    reviewer,
                    knowledge_id,
                    now,
                    case_id,
                ),
            )
    return dict(counts)


def _is_feedback(caption: str) -> bool:
    meaningful = _meaningful_text(caption)
    if not meaningful:
        return False
    if FEEDBACK_RE.search(meaningful):
        return True
    # Supervisor captions commonly start with an employee name followed by a concrete judgment.
    lines = [line.strip() for line in caption.splitlines() if line.strip()]
    return len(lines) >= 2 and len(meaningful) >= 24


def detect_supervisor(messages: list[WhatsAppMessage]) -> str | None:
    """Choose the sender with the largest number of explicit feedback captions."""
    counts = Counter(
        message.sender
        for message in messages
        if message.sender and _is_feedback(_caption(message.content)[1])
        and (ATTACHED_RE.search(message.content) or "<Media omitted>" in message.content)
    )
    return counts.most_common(1)[0][0] if counts else None


def classify_message(message: WhatsAppMessage, supervisor_sender: str | None) -> str | None:
    """Return a conservative import category; uncertain items never become approved knowledge."""
    filename, caption = _caption(message.content)
    omitted = "<Media omitted>" in message.content
    if not filename and not omitted:
        return None
    is_supervisor = bool(supervisor_sender and message.sender == supervisor_sender)
    meaningful = _meaningful_text(caption)
    if omitted:
        if is_supervisor and _is_feedback(caption):
            return "rule_candidate_missing_image"
        return None
    if is_supervisor and _is_feedback(caption):
        return "supervisor_case"
    if is_supervisor and caption and not meaningful:
        return "link_only"
    if is_supervisor:
        return "review_required"
    if not caption:
        return "report_or_unrelated"
    return "review_required"


def _safe_archive_images(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.name in {"", ".", ".."} or ".." in path.parts:
            continue
        if Path(path.name).suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        if info.file_size > 25 * 1024 * 1024:
            continue
        result[path.name.casefold()] = info
    return result


def import_whatsapp_supervisor_zip(
    database: Database,
    zip_path: str | Path,
    *,
    supervisor_sender: str | None = None,
    storage_root: str | Path | None = None,
) -> dict[str, Any]:
    """Import an export idempotently and copy only referenced images into local storage."""
    source = Path(zip_path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".zip":
        raise ValueError("Choose an existing WhatsApp ZIP export")
    if source.stat().st_size > 500 * 1024 * 1024:
        raise ValueError("WhatsApp ZIP is larger than the 500 MB safety limit")
    digest = _archive_hash(source)
    existing = database.supervisor_import_by_hash(digest)
    if existing:
        return {**existing, "duplicate": True}

    import_id = digest[:16]
    root = Path(storage_root or database.path.parent / "supervisor-knowledge").resolve()
    media_dir = root / import_id / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as archive:
        text_entries = [item for item in archive.infolist() if item.filename.casefold().endswith(".txt")]
        if not text_entries:
            raise ValueError("The ZIP does not contain a WhatsApp TXT export")
        text = archive.read(text_entries[0]).decode("utf-8-sig", errors="replace")
        messages = parse_whatsapp_export(text)
        supervisor = supervisor_sender.strip() if supervisor_sender else detect_supervisor(messages)
        if not supervisor:
            raise ValueError("Could not identify the supervisor; enter the WhatsApp sender name")
        archive_images = _safe_archive_images(archive)
        records: list[dict[str, Any]] = []
        media_references = 0
        missing_references = 0
        for message in messages:
            has_media = bool(ATTACHED_RE.search(message.content) or "<Media omitted>" in message.content)
            if not has_media:
                continue
            media_references += 1
            filename, caption = _caption(message.content)
            category = classify_message(message, supervisor)
            if not category:
                if "<Media omitted>" in message.content:
                    missing_references += 1
                continue
            stored_path: str | None = None
            if filename:
                info = archive_images.get(filename.casefold())
                if info:
                    destination = media_dir / filename
                    with archive.open(info) as source_file, destination.open("wb") as target_file:
                        shutil.copyfileobj(source_file, target_file)
                    stored_path = str(destination)
                else:
                    category = "rule_candidate_missing_image" if _is_feedback(caption) else "review_required"
                    missing_references += 1
            else:
                missing_references += 1
            records.append(
                {
                    "message_index": message.index,
                    "message_date": message.date,
                    "message_time": message.time,
                    "sender": message.sender,
                    "image_filename": filename,
                    "image_path": stored_path,
                    "caption_original": caption,
                    "auto_category": category,
                    "auto_reason": {
                        "supervisor_case": "Supervisor message contains an image and a concrete judgment.",
                        "rule_candidate_missing_image": "Supervisor judgment is available but the image is missing.",
                        "link_only": "The image caption contains only a link or mention.",
                        "report_or_unrelated": "Media was posted without a supervisor judgment.",
                        "review_required": "The item could not be classified confidently.",
                    }[category],
                }
            )

    stats = Counter(record["auto_category"] for record in records)
    database.create_supervisor_import(
        {
            "id": import_id,
            "source_zip": str(source),
            "archive_sha256": digest,
            "chat_filename": text_entries[0].filename,
            "supervisor_sender": supervisor,
            "message_count": len(messages),
            "media_reference_count": media_references,
            "extracted_image_count": sum(bool(record["image_path"]) for record in records),
            "missing_media_count": missing_references,
            "stats": dict(stats),
        },
        records,
    )
    return {
        "id": import_id,
        "source_zip": str(source),
        "supervisor_sender": supervisor,
        "message_count": len(messages),
        "media_reference_count": media_references,
        "extracted_image_count": sum(bool(record["image_path"]) for record in records),
        "missing_media_count": missing_references,
        "case_count": len(records),
        "stats": dict(stats),
        "duplicate": False,
    }
