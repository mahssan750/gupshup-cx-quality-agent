"""Resolve the automatic settings for a folder-based quality run."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_DATE_RANGE = re.compile(
    r"(?P<from>\d{4}-\d{2}-\d{2})-to-(?P<to>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def infer_run_dates(zip_folder: str | Path, *, today: date | None = None) -> tuple[str, str]:
    """Infer a reporting range from the ZIP folder or one of its parent folder names."""

    folder = Path(zip_folder)
    for candidate in (folder, *folder.parents):
        match = _DATE_RANGE.search(candidate.name)
        if not match:
            continue
        from_date = date.fromisoformat(match.group("from"))
        to_date = date.fromisoformat(match.group("to"))
        if from_date <= to_date:
            return from_date.isoformat(), to_date.isoformat()
    # Dubai is UTC+04:00 year-round, so this remains portable on Windows systems
    # that do not have the optional IANA tzdata package installed.
    current = today or datetime.now(timezone(timedelta(hours=4))).date()
    return current.isoformat(), current.isoformat()


def configured_roster_path() -> Path | None:
    """Return the optional centrally configured roster used by every new run."""

    value = os.environ.get("CXQUALITY_ROSTER", "").strip()
    return Path(value).expanduser() if value else None
