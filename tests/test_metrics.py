from dataclasses import replace

from cxquality.ingest import parse_transcript_text
from cxquality.metrics import compute_metrics, compute_weekly_metrics

TEXT = """CHAT TRANSCRIPT
Customer Name: Ahmed
Chat Started At: 03 Nov 2026 10:00:00
Last Message On: 03 Nov 2026 10:10:00
Session Status: CLOSED
03 Nov 2026 10:00:00 | System User : Session 123 started
03 Nov 2026 10:00:10 | Ahmed : Hello, I need help with my order
03 Nov 2026 10:01:00 | System User : John assigned to John of team DEFAULT via transfer action manual assign
03 Nov 2026 10:01:30 | John : Welcome, please share the order number
03 Nov 2026 10:02:00 | Ahmed : It is 55
03 Nov 2026 10:03:00 | John : I found it and it ships today
03 Nov 2026 10:04:00 | System User : Jane assigned to Jane of team DEFAULT via transfer action manual assign
03 Nov 2026 10:10:00 | Ahmed : Thank you
"""


def test_objective_metrics_and_eligibility():
    transcript = parse_transcript_text(TEXT, session_id="123")
    metrics = compute_metrics(transcript, weekly_sessions_in_thread=2)
    assert metrics.queue_wait_seconds == 60
    assert metrics.assignment_to_first_reply_seconds == 30
    assert metrics.first_human_response_seconds == 90
    assert metrics.median_reply_seconds == 70
    assert metrics.p90_reply_seconds == 80
    assert metrics.unanswered_customer_turns == 1
    assert metrics.transfer_count == 1
    assert metrics.primary_agent_alias == "Jane"
    assert metrics.eligible
    assert metrics.repeat_contact


def test_control_only_chat_is_not_eligible():
    text = TEXT.replace("Hello, I need help with my order", "STOP").replace(
        "It is 55", "[IMAGE]"
    ).replace("Thank you", "STOP")
    metrics = compute_metrics(parse_transcript_text(text, session_id="123"))
    assert not metrics.eligible
    assert metrics.exclusion_reason == "control_or_media_only"


def test_weekly_repeat_count_uses_anonymous_thread():
    first = parse_transcript_text(TEXT, session_id="123")
    second = parse_transcript_text(TEXT.replace("Session 123", "Session 456"), session_id="456")
    first = replace(first, customer_thread_id="thread_a")
    second = replace(second, customer_thread_id="thread_a")
    result = compute_weekly_metrics([first, second])
    assert [item.weekly_sessions_in_thread for item in result] == [2, 2]

