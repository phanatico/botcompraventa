"""Helpers for clean date / time formatting in user-facing messages and admin views."""
from datetime import datetime, timezone
from typing import Any


def _coerce_dt(value: Any) -> datetime | None:
    """Best-effort conversion of arbitrary input into an aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        # datetime.fromisoformat handles "+00:00" and microseconds.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_dt(value: Any, fallback: str = "—") -> str:
    """Pretty datetime in `dd/mm/yyyy HH:MM` (UTC). Hides microseconds and timezone offset."""
    dt = _coerce_dt(value)
    if not dt:
        return fallback
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M")


def format_date(value: Any, fallback: str = "—") -> str:
    """Pretty date in `dd/mm/yyyy` (UTC)."""
    dt = _coerce_dt(value)
    if not dt:
        return fallback
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y")


def days_left(value: Any) -> int | None:
    """Days remaining from now (UTC) until `value`. Returns None if no valid date."""
    dt = _coerce_dt(value)
    if not dt:
        return None
    delta = dt - datetime.now(timezone.utc)
    return max(int(delta.total_seconds() // 86400), 0) if delta.total_seconds() > 0 else 0


def days_left_str(value: Any, fallback: str = "—") -> str:
    n = days_left(value)
    return fallback if n is None else str(n)
