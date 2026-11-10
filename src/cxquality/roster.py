"""Canonical staff roster and conservative transcript-alias matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path

from openpyxl import load_workbook


def normalize_alias(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("·", " ")
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", text)


def _is_latin_name(value: str) -> bool:
    return bool(re.search(r"[a-z]", value, re.IGNORECASE)) and not bool(re.search(r"\d", value))


def _clean(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").replace("·", " ")).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class AgentRecord:
    canonical_name: str
    staff_id: str | None = None
    country: str | None = None
    branch: str | None = None
    canonical_name_en: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    active: bool = True
    source_row: int | None = None

    def all_names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item
                for item in (self.canonical_name, self.canonical_name_en, *self.aliases)
                if item
            )
        )


@dataclass(frozen=True, slots=True)
class AliasSuggestion:
    input_alias: str
    status: str
    confidence: float
    agent: AgentRecord | None
    reason: str


_HEADER_KEYS = {
    "name": {"الموظف", "الاسم", "اسم الموظف", "employee", "agent", "name"},
    "staff_id": {"الرقم الوظيفي", "رقم الموظف", "staff id", "employee id", "id"},
    "country": {"الدولة", "country"},
    "branch": {"الفرع", "branch"},
    "alias": {"alias", "username", "اسم المستخدم", "معرف الشات"},
}


def _header_role(value: object) -> str | None:
    key = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    for role, labels in _HEADER_KEYS.items():
        if key in {item.casefold() for item in labels}:
            return role
    return None


def _normalize_staff_id(value: object) -> tuple[str | None, bool]:
    text = _clean(value)
    if not text:
        return None, True
    if text.casefold() in {"إجازة".casefold(), "اجازة".casefold(), "leave", "vacation"}:
        return None, False
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    return text, True


def _english_name(canonical: str, aliases: Iterable[str]) -> str | None:
    candidates = [canonical, *aliases]
    for item in candidates:
        cleaned = re.sub(r"\bdnp\b|dnp$|^dnp", "", item, flags=re.IGNORECASE).strip(" ._-")
        if _is_latin_name(cleaned) and len(re.sub(r"[^a-z]", "", cleaned, flags=re.IGNORECASE)) >= 4:
            return re.sub(r"[._]+", " ", cleaned).strip().title()
    return None


def load_roster_xlsx(
    path: str | Path,
    *,
    sheet_name: str | None = None,
    manual_overrides: Iterable[Mapping[str, object]] = (),
) -> list[AgentRecord]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[sheet_name] if sheet_name else workbook[workbook.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    columns: dict[str, int] = {}
    for index, value in enumerate(rows[0]):
        if role := _header_role(value):
            columns.setdefault(role, index)
    columns.setdefault("name", 0)
    columns.setdefault("staff_id", 1)
    columns.setdefault("country", 2)
    columns.setdefault("branch", 3)

    records: list[AgentRecord] = []
    for row_number, row in enumerate(rows[1:], start=2):
        name = _clean(row[columns["name"]] if len(row) > columns["name"] else None)
        if not name:
            continue
        staff_id, active = _normalize_staff_id(
            row[columns["staff_id"]] if len(row) > columns["staff_id"] else None
        )
        aliases: list[str] = []
        if "alias" in columns and len(row) > columns["alias"]:
            if alias := _clean(row[columns["alias"]]):
                aliases.append(alias)
        else:
            for value in row[4:]:
                candidate = _clean(value)
                if candidate and candidate.casefold() not in {"إجازة".casefold(), "leave"}:
                    aliases.append(candidate)
        records.append(
            AgentRecord(
                canonical_name=name,
                staff_id=staff_id,
                country=_clean(row[columns["country"]]) if len(row) > columns["country"] else None,
                branch=_clean(row[columns["branch"]]) if len(row) > columns["branch"] else None,
                canonical_name_en=_english_name(name, aliases),
                aliases=tuple(dict.fromkeys(aliases)),
                active=active,
                source_row=row_number,
            )
        )
    return apply_manual_overrides(records, manual_overrides)


def apply_manual_overrides(
    records: Iterable[AgentRecord], overrides: Iterable[Mapping[str, object]]
) -> list[AgentRecord]:
    output = list(records)
    for override in overrides:
        staff_id = _clean(override.get("staff_id"))
        name = _clean(override.get("canonical_name") or override.get("name"))
        match_index = next(
            (
                index
                for index, record in enumerate(output)
                if (staff_id and record.staff_id == staff_id)
                or (name and normalize_alias(record.canonical_name) == normalize_alias(name))
            ),
            None,
        )
        base = output[match_index] if match_index is not None else AgentRecord(name or "Unknown")
        manual_aliases = [_clean(item) for item in override.get("aliases", ())]
        aliases = tuple(
            dict.fromkeys(
                item
                for item in (*base.aliases, base.canonical_name, *manual_aliases)
                if item and normalize_alias(item) != normalize_alias(name or base.canonical_name)
            )
        )
        updated = replace(
            base,
            canonical_name=name or base.canonical_name,
            staff_id=staff_id if staff_id is not None else base.staff_id,
            country=_clean(override.get("country")) or base.country,
            branch=_clean(override.get("branch")) or base.branch,
            canonical_name_en=_clean(override.get("canonical_name_en")) or base.canonical_name_en,
            aliases=aliases,
            active=bool(override.get("active", base.active)),
        )
        if match_index is None:
            output.append(updated)
        else:
            output[match_index] = updated
    return output


def suggest_alias(
    input_alias: str,
    records: Iterable[AgentRecord],
    *,
    threshold: float = 0.82,
    ambiguity_margin: float = 0.06,
    manual_alias_map: Mapping[str, str] | None = None,
) -> AliasSuggestion:
    records = list(records)
    key = normalize_alias(input_alias)
    if not key:
        return AliasSuggestion(input_alias, "unmatched", 0.0, None, "empty alias")

    if manual_alias_map and key in {normalize_alias(k) for k in manual_alias_map}:
        mapped = next(value for alias, value in manual_alias_map.items() if normalize_alias(alias) == key)
        record = next(
            (
                item
                for item in records
                if item.staff_id == str(mapped)
                or normalize_alias(item.canonical_name) == normalize_alias(mapped)
            ),
            None,
        )
        return AliasSuggestion(
            input_alias,
            "matched" if record else "unmatched",
            1.0 if record else 0.0,
            record,
            "manual override" if record else "manual target not found",
        )

    ranked: list[tuple[float, AgentRecord, str]] = []
    for record in records:
        for candidate in record.all_names():
            candidate_key = normalize_alias(candidate)
            if not candidate_key:
                continue
            score = 1.0 if key == candidate_key else SequenceMatcher(None, key, candidate_key).ratio()
            ranked.append((score, record, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return AliasSuggestion(input_alias, "unmatched", 0.0, None, "roster has no aliases")
    best_score, best_record, best_name = ranked[0]
    second_score = next(
        (score for score, record, _ in ranked[1:] if record != best_record), 0.0
    )
    if best_score < threshold:
        return AliasSuggestion(input_alias, "unmatched", best_score, None, "below match threshold")
    if best_score < 1.0 and best_score - second_score < ambiguity_margin:
        return AliasSuggestion(input_alias, "review", best_score, best_record, "ambiguous fuzzy match")
    return AliasSuggestion(input_alias, "matched", best_score, best_record, f"matched {best_name}")

