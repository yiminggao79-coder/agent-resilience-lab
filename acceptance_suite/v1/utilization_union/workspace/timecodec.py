"""Decode external timestamps at the reporting boundary."""

from datetime import datetime, timezone


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
