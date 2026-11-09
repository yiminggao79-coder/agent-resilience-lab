"""Local wall-clock parsing for a single calendar day."""
import re
from datetime import date

def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("time must be HH:MM")
    hour, minute = map(int, value.split(":"))
    if hour == 24 and minute == 0:
        return 1440
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("time out of range")
    return hour * 60 + minute

def day_key(value):
    if not isinstance(value, str):
        raise ValueError("day must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("day must be an ISO date") from None
    if parsed.isoformat() != value:
        raise ValueError("day must be YYYY-MM-DD")
    return value
