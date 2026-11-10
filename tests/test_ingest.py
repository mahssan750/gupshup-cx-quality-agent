import zipfile
from pathlib import Path

import pytest

from cxquality.ingest import (
    IngestFailure,
    PrimaryPdfNotFound,
    build_customer_threads,
    ingest_directory,
    inspect_zip,
    normalize_transcript_text,
    parse_transcript_text,
    parse_zip,
)

TRANSCRIPT = """CHAT TRANSCRIPT
Customer Name: Ahmed
Chat Started At: 03 Nov 2026 10:00:00
Last Message On: 03 Nov 2026 10:05:00
Session Status: CLOSED
03 Nov 2026 10:00:00 | System User : Session 123 started
03 Nov 2026 10:00:05 | Ahmed : Hello, I need help
03 Nov 2026 10:01:00 | System User : John Doe assigned to John Doe of team DEFAULT via transfer action manual assign
03 Nov 2026 10:01:10 | John Doe : Hello Ahmed,
how can I help?
03 Nov 2026 10:05:00 | Ahmed : Thank you
Exported from Agent Assist on 09 Nov 2026 11:55:07
"""


def _zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"fake-pdf")


def test_parse_transcript_roles_assignment_and_wrapped_lines():
    transcript = parse_transcript_text(TRANSCRIPT, session_id="123")
    assert transcript.customer_name == "Ahmed"
    assert [item.role for item in transcript.messages] == [
        "system",
        "customer",
        "system",
        "agent",
        "customer",
    ]
    assert transcript.messages[3].text.endswith("how can I help?")
    assert transcript.assigned_agents == ("John Doe",)
    assert "Exported from Agent Assist" not in transcript.normalized_text


def test_punctuation_customer_name_and_bidi_marks_are_preserved_as_customer():
    text = TRANSCRIPT.replace("Customer Name: Ahmed", "Customer Name: .").replace(
        "| Ahmed :", "| \u200f. :"
    )
    transcript = parse_transcript_text(text, session_id="123")
    assert [item.role for item in transcript.messages].count("customer") == 2


def test_rtl_visual_order_header_and_message_are_repaired():
    text = TRANSCRIPT.replace(
        "Customer Name: Ahmed", "\u202bمحمد بيت القبايل \u202aCustomer Name:\u202c\u202c"
    ).replace(
        "03 Nov 2026 10:00:05 | Ahmed : Hello, I need help",
        "\u202bاحتاج مساعدة \u202a03 Nov 2026 10:00:05 | محمد بيت القبايل :\u202c\u202c",
    ).replace("| Ahmed : Thank you", "| محمد بيت القبايل : Thank you")
    transcript = parse_transcript_text(text, session_id="123")
    assert transcript.customer_name == "محمد بيت القبايل"
    assert transcript.messages[1].role == "customer"
    assert transcript.messages[1].text == "احتاج مساعدة"


def test_rtl_sender_before_timestamp_is_repaired_without_ocr():
    text = TRANSCRIPT.replace(
        "03 Nov 2026 10:00:05 | Ahmed : Hello, I need help",
        "Hello, I need help : Ahmed | 03 Nov 2026 10:00:05",
    )
    transcript = parse_transcript_text(text, session_id="123")
    assert transcript.messages[1].role == "customer"
    assert transcript.messages[1].text == "Hello, I need help"


def test_normalize_export_timestamp_makes_regenerations_equal():
    left = normalize_transcript_text(TRANSCRIPT)
    right = normalize_transcript_text(TRANSCRIPT.replace("11:55:07", "12:01:00"))
    assert left == right


def test_primary_pdf_selected_and_history_only_indexed(tmp_path):
    path = tmp_path / "123.zip"
    _zip(path, ["111_58694.pdf", "123_58694.pdf", "older.txt"])
    index = inspect_zip(path)
    assert index.primary_entry == "123_58694.pdf"
    assert index.history_session_ids == ("111", "123")
    transcript = parse_zip(path, extractor=lambda _: TRANSCRIPT)
    assert transcript.session_id == "123"
    assert transcript.history_session_ids == ("111", "123")


def test_missing_primary_pdf_is_error(tmp_path):
    path = tmp_path / "999.zip"
    _zip(path, ["111_58694.pdf"])
    with pytest.raises(PrimaryPdfNotFound):
        inspect_zip(path)


def test_directory_ingestion_isolates_one_bad_transcript(tmp_path):
    path = tmp_path / "999.zip"
    _zip(path, ["999_58694.pdf"])
    results = list(ingest_directory(tmp_path, extractor=lambda _: "not a transcript"))
    assert len(results) == 1
    assert isinstance(results[0], IngestFailure)
    assert results[0].session_id == "999"
    assert results[0].error_type == "TranscriptParseError"


def test_customer_threads_use_history_graph_without_pii(tmp_path):
    first = tmp_path / "101.zip"
    second = tmp_path / "202.zip"
    third = tmp_path / "303.zip"
    _zip(first, ["101_1.pdf", "202_1.pdf"])
    _zip(second, ["202_1.pdf", "303_1.pdf"])
    _zip(third, ["303_1.pdf", "202_1.pdf"])
    mapping = build_customer_threads([first, second, third])
    assert mapping["101"] == mapping["202"] == mapping["303"]
    assert mapping["101"].startswith("thread_")

