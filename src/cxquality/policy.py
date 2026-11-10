"""Small, auditable helpers for an updateable policy knowledge base."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_NUMBERED_ITEM = re.compile(r"(?ms)^\s*(\d+)\s*[-\u2013\u2014]\s*(.+?)(?=^\s*\d+\s*[-\u2013\u2014]|\Z)")
_TOKEN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOPWORDS = {
    "في",
    "من",
    "على",
    "او",
    "أو",
    "الي",
    "إلى",
    "عن",
    "هو",
    "هي",
    "هذا",
    "هذه",
    "يجب",
    "عدم",
    "العميل",
    "للعميل",
    "the",
    "and",
    "for",
    "with",
    "from",
}


@dataclass(frozen=True, slots=True)
class PolicyItem:
    external_id: str
    title: str
    body: str


def parse_numbered_policies(text: str) -> list[PolicyItem]:
    """Parse ``1- ...`` style Arabic or English training notes without rewriting them."""

    items: list[PolicyItem] = []
    for number, body in _NUMBERED_ITEM.findall(text.replace("\r\n", "\n")):
        cleaned = re.sub(r"\s+", " ", body).strip()
        if not cleaned:
            continue
        title = cleaned[:96].rstrip()
        if len(cleaned) > 96:
            title += "..."
        items.append(PolicyItem(external_id=number, title=title, body=cleaned))
    return items


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN.findall(value)
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    }


def select_relevant_policies(
    transcript: str, policies: Iterable[PolicyItem], *, limit: int = 6
) -> list[PolicyItem]:
    """Return the most lexically relevant policies; sentiment scoring does not depend on this."""

    transcript_tokens = _tokens(transcript)
    scored: list[tuple[float, PolicyItem]] = []
    for item in policies:
        policy_tokens = _tokens(item.body)
        overlap = transcript_tokens & policy_tokens
        if not overlap:
            continue
        score = len(overlap) / max(1.0, len(policy_tokens) ** 0.5)
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], int(pair[1].external_id) if pair[1].external_id.isdigit() else 0))
    return [item for _, item in scored[:limit]]

