"""Objective, reproducible conversation metrics (no AI judgments)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import ceil
from statistics import median

from .ingest import Message, Transcript, normalize_identity

_MEDIA_ONLY = re.compile(r"^\[(?:IMAGE|AUDIO|DOC|VIDEO|STICKER|LOCATION)\]$", re.IGNORECASE)
_CONTROL_ONLY = {
    "stop",
    "start",
    "subscribe",
    "unsubscribe",
    "الغاء",
    "إلغاء",
    "اشتراك",
}


@dataclass(frozen=True, slots=True)
class OperationalMetrics:
    session_id: str
    customer_thread_id: str | None
    primary_agent_alias: str | None
    assigned_agent_count: int
    customer_messages: int
    customer_turns: int
    agent_messages: int
    bot_messages: int
    system_messages: int
    queue_wait_seconds: float | None
    assignment_to_first_reply_seconds: float | None
    first_human_response_seconds: float | None
    median_reply_seconds: float | None
    p90_reply_seconds: float | None
    unanswered_customer_turns: int
    transfer_count: int
    handling_duration_seconds: float
    eligible: bool
    exclusion_reason: str | None
    weekly_sessions_in_thread: int
    repeat_contact: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _substantive(message: Message) -> bool:
    text = re.sub(r"\s+", " ", message.text).strip()
    if not text or _MEDIA_ONLY.fullmatch(text) or text.casefold() in _CONTROL_ONLY:
        return False
    return bool(re.search(r"[A-Za-z\u0600-\u06ff0-9]", text))


def _customer_turns(messages: tuple[Message, ...]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    current: list[Message] = []
    for message in messages:
        if message.role == "customer":
            current.append(message)
        elif current and message.role in {"agent", "bot"}:
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _reply_latencies(transcript: Transcript) -> tuple[list[float], int]:
    messages = transcript.messages
    latencies: list[float] = []
    unanswered = 0
    index = 0
    while index < len(messages):
        if messages[index].role != "customer":
            index += 1
            continue
        final_customer = messages[index]
        index += 1
        while index < len(messages) and messages[index].role in {"customer", "system", "bot"}:
            if messages[index].role == "customer":
                final_customer = messages[index]
            index += 1
        next_agent = messages[index] if index < len(messages) and messages[index].role == "agent" else None
        if next_agent:
            latencies.append(max(0.0, (next_agent.timestamp - final_customer.timestamp).total_seconds()))
        else:
            unanswered += 1
    return latencies, unanswered


def _percentile90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(0.9 * len(ordered)) - 1)]


def compute_metrics(
    transcript: Transcript, *, weekly_sessions_in_thread: int = 1
) -> OperationalMetrics:
    customer = [item for item in transcript.messages if item.role == "customer"]
    agents = [item for item in transcript.messages if item.role == "agent"]
    bot = [item for item in transcript.messages if item.role == "bot"]
    system = [item for item in transcript.messages if item.role == "system"]
    turns = _customer_turns(transcript.messages)
    latencies, unanswered = _reply_latencies(transcript)

    first_assignment = transcript.assignments[0] if transcript.assignments else None
    first_agent = agents[0] if agents else None
    queue_wait = (
        max(0.0, (first_assignment.timestamp - transcript.started_at).total_seconds())
        if first_assignment
        else None
    )
    assignment_reply = (
        max(0.0, (first_agent.timestamp - first_assignment.timestamp).total_seconds())
        if first_assignment and first_agent and first_agent.timestamp >= first_assignment.timestamp
        else None
    )
    first_response = (
        max(0.0, (first_agent.timestamp - transcript.started_at).total_seconds())
        if first_agent
        else None
    )

    assignment_aliases: list[str] = []
    for event in transcript.assignments:
        if not assignment_aliases or normalize_identity(assignment_aliases[-1]) != normalize_identity(
            event.agent_alias
        ):
            assignment_aliases.append(event.agent_alias)

    substantive_customer = any(_substantive(item) for item in customer)
    if not customer:
        reason = "no_customer_messages"
    elif not substantive_customer:
        reason = "control_or_media_only"
    elif any(re.search(r"\[(?:IMAGE|PHOTO|DOC|DOCUMENT|PDF|AUDIO|VIDEO|STICKER|LOCATION)\]",
                       item.text, re.IGNORECASE) for item in transcript.messages):
        reason = "contains_attachment"
    elif not agents:
        reason = "no_human_agent_reply"
    else:
        reason = None

    return OperationalMetrics(
        session_id=transcript.session_id,
        customer_thread_id=transcript.customer_thread_id,
        primary_agent_alias=assignment_aliases[-1] if assignment_aliases else (
            agents[0].sender if agents else None
        ),
        assigned_agent_count=len(assignment_aliases),
        customer_messages=len(customer),
        customer_turns=len(turns),
        agent_messages=len(agents),
        bot_messages=len(bot),
        system_messages=len(system),
        queue_wait_seconds=queue_wait,
        assignment_to_first_reply_seconds=assignment_reply,
        first_human_response_seconds=first_response,
        median_reply_seconds=float(median(latencies)) if latencies else None,
        p90_reply_seconds=_percentile90(latencies),
        unanswered_customer_turns=unanswered,
        transfer_count=max(0, len(assignment_aliases) - 1),
        handling_duration_seconds=max(
            0.0, (transcript.last_message_at - transcript.started_at).total_seconds()
        ),
        eligible=reason is None,
        exclusion_reason=reason,
        weekly_sessions_in_thread=max(1, weekly_sessions_in_thread),
        repeat_contact=weekly_sessions_in_thread > 1,
    )


def compute_weekly_metrics(transcripts: Iterable[Transcript]) -> list[OperationalMetrics]:
    items = list(transcripts)
    thread_counts = Counter(
        item.customer_thread_id or f"session:{item.session_id}" for item in items
    )
    return [
        compute_metrics(
            item,
            weekly_sessions_in_thread=thread_counts[
                item.customer_thread_id or f"session:{item.session_id}"
            ],
        )
        for item in items
    ]

