"""Deterministic Gupshup ZIP/PDF ingestion with no OCR path."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

DATE_FORMAT = "%d %b %Y %H:%M:%S"
_EXPORT_LINE = re.compile(r"(?im)^\s*Exported from Agent Assist on .*?\s*$")
_BIDI_CONTROLS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_TIMESTAMP_TEXT = r"\d{2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}"
_MESSAGE_START = re.compile(
    r"(?m)^\s*(?P<timestamp>\d{2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2})"
    r"\s*\|\s*(?P<sender>.*?)\s*:\s*"
)
_HEADER = re.compile(
    r"CHAT\s+TRANSCRIPT\s+Customer\s+Name:\s*(?P<customer>.*?)\s+"
    r"Chat\s+Started\s+At:\s*(?P<started>\d{2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\s+"
    r"Last\s+Message\s+On:\s*(?P<last>\d{2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\s+"
    r"Session\s+Status:\s*(?P<status>[^\r\n]+)",
    re.IGNORECASE | re.DOTALL,
)
_ASSIGNMENT = re.compile(
    r"(?P<actor>.+?)\s+assigned\s+to\s+(?P<agent>.+?)\s+of\s+team\s+"
    r"(?P<team>.+?)(?:\s+via\s+transfer\s+action\s+(?P<action>.+))?$",
    re.IGNORECASE | re.DOTALL,
)


class IngestError(RuntimeError):
    pass


class PrimaryPdfNotFound(IngestError):
    pass


class TextExtractionError(IngestError):
    """Extraction failed; callers must not fall back to OCR."""


class TranscriptParseError(IngestError):
    pass


@dataclass(frozen=True, slots=True)
class IngestFailure:
    """A file-level extraction failure that must not cancel a weekly run."""

    session_id: str
    source_zip: Path
    error_type: str


@dataclass(frozen=True, slots=True)
class Message:
    timestamp: datetime
    sender: str
    text: str
    role: str


@dataclass(frozen=True, slots=True)
class AssignmentEvent:
    timestamp: datetime
    agent_alias: str
    actor_alias: str
    team: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class ZipIndex:
    path: Path
    primary_session_id: str
    primary_entry: str
    history_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Transcript:
    session_id: str
    customer_name: str
    started_at: datetime
    last_message_at: datetime
    status: str
    messages: tuple[Message, ...]
    assignments: tuple[AssignmentEvent, ...]
    source_zip: Path | None = None
    primary_entry: str | None = None
    history_session_ids: tuple[str, ...] = field(default_factory=tuple)
    customer_thread_id: str | None = None
    normalized_text: str = ""

    @property
    def assigned_agents(self) -> tuple[str, ...]:
        result: list[str] = []
        for event in self.assignments:
            if not result or normalize_identity(result[-1]) != normalize_identity(event.agent_alias):
                result.append(event.agent_alias)
        return tuple(result)


def normalize_identity(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w\u0600-\u06ff]+", "", value, flags=re.UNICODE)


def _raw_identity(value: str) -> str:
    """Comparable display identity, retaining punctuation-only customer names."""

    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_transcript_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _BIDI_CONTROLS.sub("", text)
    text = text.replace("\ufeff", "").replace("\f", "\n")
    text = _EXPORT_LINE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    repaired: list[str] = []
    rtl_message = re.compile(
        rf"^\s*(?P<body>.+?)\s+(?P<timestamp>{_TIMESTAMP_TEXT})\s*\|\s*"
        r"(?P<sender>.*?)\s*:\s*$"
    )
    rtl_message_sender_before_timestamp = re.compile(
        rf"^\s*(?P<body>.+)\s+:\s*(?P<sender>[^|]+?)\s*\|\s*"
        rf"(?P<timestamp>{_TIMESTAMP_TEXT})\s*$"
    )
    for line in text.splitlines():
        if "Customer Name:" in line:
            before, after = line.split("Customer Name:", 1)
            candidate = after.strip() or before.strip()
            line = f"Customer Name: {candidate}"
        elif (match := rtl_message.match(line)) or (
            match := rtl_message_sender_before_timestamp.match(line)
        ):
            line = (
                f"{match.group('timestamp')} | {match.group('sender')} : "
                f"{match.group('body')}"
            )
        repaired.append(line)
    text = "\n".join(repaired)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _message_role(sender: str, customer_name: str) -> str:
    key = normalize_identity(sender)
    if key in {"systemuser", "system"}:
        return "system"
    if key in {"bot", "chatbot"}:
        return "bot"
    # Some real customers use punctuation-only names (for example ".").
    # Exact cleaned display equality must therefore precede the alphanumeric key.
    raw_sender, raw_customer = _raw_identity(sender), _raw_identity(customer_name)
    if raw_sender and raw_sender == raw_customer:
        return "customer"
    if key and key == normalize_identity(customer_name):
        return "customer"
    return "agent"


def _assignment(message: Message) -> AssignmentEvent | None:
    if message.role != "system":
        return None
    match = _ASSIGNMENT.search(_clean(message.text))
    if not match:
        return None
    return AssignmentEvent(
        message.timestamp,
        _clean(match.group("agent")),
        _clean(match.group("actor")),
        _clean(match.group("team")),
        _clean(match.group("action")) if match.group("action") else None,
    )


def parse_transcript_text(
    text: str, *, session_id: str | None = None, source_zip: Path | None = None
) -> Transcript:
    normalized = normalize_transcript_text(text)
    header = _HEADER.search(normalized)
    if not header:
        raise TranscriptParseError("Transcript header was not found")
    customer = _clean(header.group("customer"))
    starts = list(_MESSAGE_START.finditer(normalized))
    messages: list[Message] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
        sender = _clean(match.group("sender"))
        messages.append(
            Message(
                datetime.strptime(match.group("timestamp"), DATE_FORMAT),
                sender,
                _clean(normalized[match.end() : end]),
                _message_role(sender, customer),
            )
        )
    if not messages:
        raise TranscriptParseError("No timestamped messages were found")
    assignments = tuple(event for item in messages if (event := _assignment(item)))
    if not session_id:
        for item in messages:
            found = re.search(r"\bSession\s+(\d+)\s+started\b", item.text, re.IGNORECASE)
            if item.role == "system" and found:
                session_id = found.group(1)
                break
    if not session_id:
        raise TranscriptParseError("Session id was not supplied or inferred")
    return Transcript(
        str(session_id),
        customer,
        datetime.strptime(header.group("started"), DATE_FORMAT),
        datetime.strptime(header.group("last"), DATE_FORMAT),
        _clean(header.group("status")),
        tuple(messages),
        assignments,
        source_zip=source_zip,
        normalized_text=normalized,
    )


def _session_id_from_entry(filename: str) -> str | None:
    name = Path(filename).name
    match = re.match(r"(\d+)(?:_.*)?\.pdf$", name, re.IGNORECASE)
    return match.group(1) if match else None


def inspect_zip(path: str | Path) -> ZipIndex:
    path = Path(path)
    expected = path.stem
    with zipfile.ZipFile(path) as archive:
        pdfs = [
            (item.filename, sid)
            for item in archive.infolist()
            if not item.is_dir() and (sid := _session_id_from_entry(item.filename))
        ]
    primary = next((name for name, sid in pdfs if sid == expected), None)
    if primary is None:
        raise PrimaryPdfNotFound(
            f"{path.name} has no PDF for primary session {expected}; found {len(pdfs)} PDFs"
        )
    history = tuple(dict.fromkeys(sid for _, sid in pdfs))
    return ZipIndex(path, expected, primary, history)


def extract_pdf_text(pdf_bytes: bytes, *, pdftotext_path: str = "pdftotext") -> str:
    """Extract embedded text using Poppler; deliberately never invoke OCR."""

    executable = (
        pdftotext_path
        if Path(pdftotext_path).is_file()
        else shutil.which(pdftotext_path)
    )
    if not executable:
        raise TextExtractionError("pdftotext was not found; OCR is disabled")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(pdf_bytes)
            temp_name = handle.name
        result = subprocess.run(
            [str(executable), "-layout", "-enc", "UTF-8", temp_name, "-"],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TextExtractionError(f"pdftotext failed: {exc}") from exc
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise TextExtractionError(f"pdftotext returned {result.returncode}: {error}")
    text = result.stdout.decode("utf-8", errors="replace")
    if len(text.strip()) < 40:
        raise TextExtractionError("PDF has no usable embedded text; OCR is disabled")
    return text


def parse_zip(
    path: str | Path,
    *,
    pdftotext_path: str = "pdftotext",
    extractor: Callable[[bytes], str] | None = None,
    customer_thread_id: str | None = None,
) -> Transcript:
    index = inspect_zip(path)
    with zipfile.ZipFile(index.path) as archive:
        pdf_bytes = archive.read(index.primary_entry)
    text = extractor(pdf_bytes) if extractor else extract_pdf_text(
        pdf_bytes, pdftotext_path=pdftotext_path
    )
    transcript = parse_transcript_text(
        text, session_id=index.primary_session_id, source_zip=index.path
    )
    return replace(
        transcript,
        primary_entry=index.primary_entry,
        history_session_ids=index.history_session_ids,
        customer_thread_id=customer_thread_id,
    )


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def build_customer_threads(paths: Iterable[str | Path]) -> dict[str, str]:
    """Connect sessions co-occurring in history ZIPs using anonymous ids."""

    graph = _UnionFind()
    for path in paths:
        ids = inspect_zip(path).history_session_ids
        if not ids:
            continue
        graph.find(ids[0])
        for session_id in ids[1:]:
            graph.union(ids[0], session_id)
    groups: dict[str, list[str]] = {}
    for session_id in graph.parent:
        groups.setdefault(graph.find(session_id), []).append(session_id)
    output: dict[str, str] = {}
    for members in groups.values():
        digest = sha256("|".join(sorted(members)).encode("ascii")).hexdigest()[:16]
        for session_id in members:
            output[session_id] = f"thread_{digest}"
    return output


def zip_paths(directory: str | Path) -> list[Path]:
    return sorted(Path(directory).glob("*.zip"), key=lambda item: item.name)


def ingest_directory(
    directory: str | Path,
    *,
    selected_paths: Sequence[str | Path] | None = None,
    pdftotext_path: str = "pdftotext",
    extractor: Callable[[bytes], str] | None = None,
) -> Iterator[Transcript | IngestFailure]:
    all_paths = zip_paths(directory)
    threads = build_customer_threads(all_paths)
    chosen = [Path(item) for item in selected_paths] if selected_paths is not None else all_paths
    for path in chosen:
        try:
            yield parse_zip(
                path,
                pdftotext_path=pdftotext_path,
                extractor=extractor,
                customer_thread_id=threads.get(path.stem),
            )
        except (IngestError, OSError, zipfile.BadZipFile) as exc:
            yield IngestFailure(path.stem, path, type(exc).__name__)
