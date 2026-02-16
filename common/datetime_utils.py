"""Shared datetime parsing helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None

    text = value.strip().replace("\x00", "")
    if not text:
        return None

    exif_prefix = text[:19]
    try:
        parsed = datetime.strptime(exif_prefix, "%Y:%m:%d %H:%M:%S")
        suffix = text[19:].strip().replace(" ", "")
        if suffix:
            if suffix == "Z":
                return parsed.replace(tzinfo=timezone.utc)
            if suffix.startswith(("+", "-")) and len(suffix) in {5, 6}:
                if len(suffix) == 5:
                    suffix = f"{suffix[:3]}:{suffix[3:]}"
                return datetime.fromisoformat(f"{parsed.isoformat()}{suffix}")
        return parsed
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def with_local_timezone_if_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=datetime.now().astimezone().tzinfo)

