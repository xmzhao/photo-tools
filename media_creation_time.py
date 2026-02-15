#!/usr/bin/env python3
"""Estimate the most likely creation time for an image or video file."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".dng",
    ".raw",
    ".arw",
    ".cr2",
    ".cr3",
    ".nef",
    ".orf",
    ".rw2",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".3gp",
    ".mts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".webm",
}


@dataclass(frozen=True)
class Candidate:
    source: str
    timestamp: datetime
    score: float
    note: str

    @property
    def iso(self) -> str:
        return self.timestamp.isoformat()


@dataclass(frozen=True)
class TimeEntry:
    source: str
    timestamp: datetime
    note: str

    @property
    def iso(self) -> str:
        return self.timestamp.isoformat()


def format_output_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def infer_path_precision(source: str) -> str | None:
    if not source.startswith("path:"):
        return None
    if "YYYYMMDD_HHMMSS" in source or "YYYY-MM-DD HH:MM:SS" in source:
        return "datetime"
    if ".YYYYMMDD." in source or ".YYYY-MM-DD." in source or source.endswith(":YYYYMMDD"):
        return "date"
    if ".YYYYMM." in source or ".YYYY-MM." in source or source.endswith(":YYYYMM"):
        return "month"
    if ".YYYY." in source or source.endswith(":YYYY"):
        return "year"
    return None


def format_output_time_for_source(source: str, value: datetime) -> str:
    precision = infer_path_precision(source)
    if precision == "year":
        return value.strftime("%Y")
    if precision == "month":
        return value.strftime("%Y-%m")
    if precision == "date":
        return value.strftime("%Y-%m-%d")
    return format_output_time(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return the most likely creation time for an image or video file."
    )
    parser.add_argument("file", help="Image/video file path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    return parser.parse_args()


def parse_datetime(value: str) -> datetime | None:
    text = value.strip().replace("\x00", "")
    if not text:
        return None

    # EXIF style: YYYY:MM:DD HH:MM:SS(+TZ)
    exif_prefix = text[:19]
    try:
        parsed = datetime.strptime(exif_prefix, "%Y:%m:%d %H:%M:%S")
        suffix = text[19:].strip()
        if suffix:
            suffix = suffix.replace(" ", "")
            if suffix == "Z":
                return parsed.replace(tzinfo=timezone.utc)
            if suffix.startswith(("+", "-")) and len(suffix) in {5, 6}:
                if len(suffix) == 5:
                    suffix = f"{suffix[:3]}:{suffix[3:]}"
                return datetime.fromisoformat(f"{parsed.isoformat()}{suffix}")
        return parsed
    except ValueError:
        pass

    # ISO style from ffprobe/exiftool JSON.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def with_local_timezone_if_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=datetime.now().astimezone().tzinfo)


def build_datetime(
    year: int,
    month: int = 1,
    day: int = 1,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute, second).replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    except ValueError:
        return None


def run_json_command(command: list[str]) -> Any | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def exiftool_candidates(file_path: Path, is_video: bool) -> tuple[list[Candidate], dict[str, str]]:
    payload = run_json_command(
        [
            "exiftool",
            "-j",
            "-s",
            "-n",
            str(file_path),
        ]
    )
    if not isinstance(payload, list) or not payload:
        return [], {}
    record = payload[0]
    if not isinstance(record, dict):
        return [], {}

    candidates: list[Candidate] = []
    details: dict[str, str] = {}
    priority = [
        ("DateTimeOriginal", 1.00, "Primary EXIF capture time"),
        ("CreateDate", 0.95, "Embedded creation time"),
        ("DateTimeDigitized", 0.92, "Digitized time"),
        ("CreationDate", 0.92, "Container creation time"),
        ("TrackCreateDate", 0.90, "Video track creation time"),
        ("MediaCreateDate", 0.90, "Video media creation time"),
        ("ModifyDate", 0.70, "Embedded modify time"),
    ]

    for field, score, note in priority:
        raw = record.get(field)
        if not isinstance(raw, str):
            continue
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        normalized = with_local_timezone_if_naive(parsed)
        details[field] = normalized.isoformat()
        candidates.append(
            Candidate(
                source=f"exiftool:{field}",
                timestamp=normalized,
                score=score if not is_video else min(score, 0.97),
                note=note,
            )
        )

    return candidates, details


def ffprobe_candidates(file_path: Path) -> tuple[list[Candidate], dict[str, str]]:
    payload = run_json_command(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
    )
    if not isinstance(payload, dict):
        return [], {}

    candidates: list[Candidate] = []
    details: dict[str, str] = {}
    format_tags = payload.get("format", {}).get("tags", {})
    if isinstance(format_tags, dict):
        raw = format_tags.get("creation_time")
        if isinstance(raw, str):
            parsed = parse_datetime(raw)
            if parsed is not None:
                normalized = with_local_timezone_if_naive(parsed)
                details["format.tags.creation_time"] = normalized.isoformat()
                candidates.append(
                    Candidate(
                        source="ffprobe:format.tags.creation_time",
                        timestamp=normalized,
                        score=0.93,
                        note="Video container creation_time tag",
                    )
                )

    streams = payload.get("streams", [])
    if isinstance(streams, list):
        for idx, stream in enumerate(streams):
            if not isinstance(stream, dict):
                continue
            tags = stream.get("tags", {})
            if not isinstance(tags, dict):
                continue
            raw = tags.get("creation_time")
            if not isinstance(raw, str):
                continue
            parsed = parse_datetime(raw)
            if parsed is None:
                continue
            normalized = with_local_timezone_if_naive(parsed)
            details[f"streams[{idx}].tags.creation_time"] = normalized.isoformat()
            candidates.append(
                Candidate(
                    source=f"ffprobe:streams[{idx}].tags.creation_time",
                    timestamp=normalized,
                    score=0.90,
                    note="Video stream creation_time tag",
                )
            )

    return candidates, details


def path_inferred_candidates(file_path: Path) -> tuple[list[Candidate], dict[str, str]]:
    entries: list[Candidate] = []

    scopes = [
        ("filename", file_path.stem, 1.00),
        ("basename", file_path.name, 0.92),
        ("parent", file_path.parent.name, 0.78),
        ("fullpath", os.fspath(file_path), 0.70),
    ]

    datetime_patterns = [
        (
            "YYYYMMDD_HHMMSS",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})[._\- ]?(0[1-9]|1[0-2])[._\- ]?"
                r"(0[1-9]|[12]\d|3[01])[T _\-]?"
                r"([01]\d|2[0-3])[._\- ]?([0-5]\d)[._\- ]?([0-5]\d)(?!\d)"
            ),
            0.58,
        ),
        (
            "YYYY-MM-DD HH:MM:SS",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])[日]?[T _\-]?"
                r"([01]?\d|2[0-3])[:._\-时](0?[0-9]|[1-5]\d)[:._\-分]"
                r"(0?[0-9]|[1-5]\d)(?:秒)?(?!\d)"
            ),
            0.56,
        ),
    ]

    date_patterns = [
        (
            "YYYYMMDD",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})(0[1-9]|1[0-2])"
                r"(0[1-9]|[12]\d|3[01])(?!\d)"
            ),
            0.54,
        ),
        (
            "YYYY-MM-DD",
            re.compile(
                r"(?<!\d)(19\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])(?:日)?(?!\d)|"
                r"(?<!\d)(20\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])(?:日)?(?!\d)"
            ),
            0.52,
        ),
    ]

    month_patterns = [
        (
            "YYYYMM",
            re.compile(r"(?<!\d)(19\d{2}|20\d{2})[./_\- ]?(0[1-9]|1[0-2])(?!\d)"),
            0.48,
        ),
        (
            "YYYY-MM",
            re.compile(r"(?<!\d)(19\d{2}|20\d{2})[./_\-年](0?[1-9]|1[0-2])(?:月)?(?!\d)"),
            0.46,
        ),
    ]

    year_pattern = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

    def push(source: str, timestamp: datetime, score: float, note: str) -> None:
        entries.append(Candidate(source=source, timestamp=timestamp, score=score, note=note))

    def path_precision_level(source: str) -> int:
        if "YYYYMMDD_HHMMSS" in source or "YYYY-MM-DD HH:MM:SS" in source:
            return 4
        if source.endswith(":YYYYMMDD") or source.endswith(":YYYY-MM-DD"):
            return 3
        if source.endswith(":YYYYMM") or source.endswith(":YYYY-MM"):
            return 2
        if source.endswith(":YYYY"):
            return 1
        return 0

    def precision_key(candidate: Candidate) -> tuple[int, int, int, int, int, int, int]:
        precision = path_precision_level(candidate.source)
        ts = candidate.timestamp
        if precision == 1:
            return (1, ts.year, 0, 0, 0, 0, 0)
        if precision == 2:
            return (2, ts.year, ts.month, 0, 0, 0, 0)
        if precision == 3:
            return (3, ts.year, ts.month, ts.day, 0, 0, 0)
        return (4, ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)

    def same_timeline_coarser(coarse: Candidate, fine: Candidate) -> bool:
        p_coarse = path_precision_level(coarse.source)
        p_fine = path_precision_level(fine.source)
        if p_coarse <= 0 or p_fine <= p_coarse:
            return False
        a = coarse.timestamp
        b = fine.timestamp
        if p_coarse == 1:
            return a.year == b.year
        if p_coarse == 2:
            return a.year == b.year and a.month == b.month
        if p_coarse == 3:
            return a.year == b.year and a.month == b.month and a.day == b.day
        return False

    for scope_name, text, scope_weight in scopes:
        if not text:
            continue

        for label, pattern, base_score in datetime_patterns:
            for idx, match in enumerate(pattern.finditer(text)):
                groups = [g for g in match.groups() if g is not None]
                if len(groups) < 6:
                    continue
                year, month, day, hour, minute, second = map(int, groups[:6])
                parsed = build_datetime(year, month, day, hour, minute, second)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Inferred from {scope_name} using {label} pattern"
                push(
                    source=source,
                    timestamp=parsed,
                    score=base_score * scope_weight,
                    note=note,
                )

        for label, pattern, base_score in date_patterns:
            for idx, match in enumerate(pattern.finditer(text)):
                groups = [g for g in match.groups() if g is not None]
                if len(groups) < 3:
                    continue
                year, month, day = map(int, groups[:3])
                parsed = build_datetime(year, month, day)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Inferred from {scope_name} using {label} pattern"
                push(
                    source=source,
                    timestamp=parsed,
                    score=base_score * scope_weight,
                    note=note,
                )

        for label, pattern, base_score in month_patterns:
            for idx, match in enumerate(pattern.finditer(text)):
                year, month = map(int, match.groups()[:2])
                parsed = build_datetime(year, month, 1)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Inferred month from {scope_name} using {label} pattern"
                push(
                    source=source,
                    timestamp=parsed,
                    score=base_score * scope_weight,
                    note=note,
                )

        for idx, match in enumerate(year_pattern.finditer(text)):
            year = int(match.group(1))
            parsed = build_datetime(year, 1, 1)
            if parsed is None:
                continue
            source = f"path:{scope_name}:YYYY"
            note = f"Inferred year from {scope_name} using YYYY pattern"
            push(
                source=source,
                timestamp=parsed,
                score=0.40 * scope_weight,
                note=note,
            )

    # 1) Remove exact duplicate inferred results (same precision + same time), keep highest score.
    by_result: dict[tuple[int, int, int, int, int, int, int], Candidate] = {}
    for candidate in sorted(entries, key=lambda c: c.score, reverse=True):
        key = precision_key(candidate)
        if key not in by_result:
            by_result[key] = candidate
    unique_results = list(by_result.values())

    # 2) If a finer-grained time exists on the same timeline, drop the coarser one.
    filtered: list[Candidate] = []
    for candidate in unique_results:
        if any(
            other is not candidate and same_timeline_coarser(candidate, other)
            for other in unique_results
        ):
            continue
        filtered.append(candidate)

    # 3) Keep conflicting times (already satisfied by the timeline compatibility check).
    filtered = sorted(filtered, key=lambda c: (c.timestamp.timestamp(), -c.score))

    details: dict[str, str] = {}
    for idx, candidate in enumerate(filtered):
        tail = candidate.source[len("path:") :] if candidate.source.startswith("path:") else candidate.source
        scope, _, label = tail.partition(":")
        detail_key = f"{scope}.{label}.{idx}"
        details[detail_key] = candidate.timestamp.isoformat()

    return filtered, details


def filesystem_candidates(file_path: Path) -> tuple[list[Candidate], dict[str, str | None]]:
    stat = file_path.stat()
    birth_time = (
        datetime.fromtimestamp(stat.st_birthtime).astimezone()
        if hasattr(stat, "st_birthtime")
        else None
    )
    mtime = datetime.fromtimestamp(stat.st_mtime).astimezone()
    ctime = datetime.fromtimestamp(stat.st_ctime).astimezone()

    fs_times = [mtime, ctime]
    if birth_time is not None:
        fs_times.append(birth_time)
    oldest = min(fs_times)

    details: dict[str, str | None] = {
        "birthtime": birth_time.isoformat() if birth_time else None,
        "mtime": mtime.isoformat(),
        "ctime": ctime.isoformat(),
        "oldest": oldest.isoformat(),
    }
    candidates = [
        Candidate(
            source="filesystem:oldest_of_birth_mtime_ctime",
            timestamp=oldest,
            score=0.65,
            note="Oldest value among birthtime/mtime/ctime",
        )
    ]
    return candidates, details


def filter_reasonable(candidates: list[Candidate]) -> list[Candidate]:
    now = datetime.now(timezone.utc).timestamp()
    min_ts = datetime(1970, 1, 1, tzinfo=timezone.utc).timestamp()
    filtered: list[Candidate] = []
    for candidate in candidates:
        ts = candidate.timestamp.astimezone(timezone.utc).timestamp()
        if ts < min_ts:
            continue
        if ts > now + 300:
            continue
        filtered.append(candidate)
    return filtered


def confidence_label(score: float) -> str:
    if score >= 0.95:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    best_by_key: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = (candidate.source, candidate.iso)
        existing = best_by_key.get(key)
        if existing is None or candidate.score > existing.score:
            best_by_key[key] = candidate
    return sorted(
        best_by_key.values(),
        key=lambda c: (c.score, c.timestamp.timestamp()),
        reverse=True,
    )


def estimate_creation_time(
    file_path: Path,
) -> tuple[Candidate, list[Candidate], dict[str, dict[str, str]], dict[str, str | None]]:
    suffix = file_path.suffix.lower()
    is_video = suffix in VIDEO_EXTENSIONS
    is_image = suffix in IMAGE_EXTENSIONS

    candidates: list[Candidate] = []
    metadata_times: dict[str, dict[str, str]] = {"exiftool": {}, "ffprobe": {}, "path": {}}

    exif_candidates, exif_details = exiftool_candidates(file_path, is_video=is_video)
    candidates.extend(exif_candidates)
    metadata_times["exiftool"] = exif_details

    if is_video:
        ffprobe_result, ffprobe_details = ffprobe_candidates(file_path)
        candidates.extend(ffprobe_result)
        metadata_times["ffprobe"] = ffprobe_details
    if not is_image and not is_video:
        # Unknown extension: still try ffprobe because some media files are atypical.
        ffprobe_result, ffprobe_details = ffprobe_candidates(file_path)
        candidates.extend(ffprobe_result)
        metadata_times["ffprobe"] = ffprobe_details

    path_candidates, path_details = path_inferred_candidates(file_path)
    candidates.extend(path_candidates)
    metadata_times["path"] = path_details

    fs_candidates, filesystem_times = filesystem_candidates(file_path)
    candidates.extend(fs_candidates)

    deduped = dedupe_candidates(filter_reasonable(candidates))
    if not deduped:
        raise RuntimeError("No valid timestamp candidates found.")
    return deduped[0], deduped, metadata_times, filesystem_times


def collect_all_times(
    candidates: list[Candidate],
    metadata_times: dict[str, dict[str, str]],
    filesystem_times: dict[str, str | None],
) -> list[TimeEntry]:
    entries: list[TimeEntry] = []
    seen: set[tuple[str, str]] = set()

    # Prefer candidate notes for user-facing explanation.
    candidate_note_by_source: dict[str, str] = {c.source: c.note for c in candidates}

    provider_default_note = {
        "exiftool": "Embedded metadata time",
        "ffprobe": "Video metadata time",
        "path": "Inferred from file path naming pattern",
    }
    for provider, fields in metadata_times.items():
        for field, iso in fields.items():
            parsed = parse_datetime(iso)
            if parsed is None:
                continue
            normalized = with_local_timezone_if_naive(parsed)
            source = f"{provider}:{field}"
            key = (source, normalized.isoformat())
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                TimeEntry(
                    source=source,
                    timestamp=normalized,
                    note=candidate_note_by_source.get(
                        source, provider_default_note.get(provider, "Metadata time")
                    ),
                )
            )

    fs_note = {
        "birthtime": "Filesystem birth time (creation in this filesystem)",
        "mtime": "Filesystem modified time",
        "ctime": "Filesystem inode/status change time",
        "oldest": "Oldest value among birthtime/mtime/ctime",
    }
    for field in ("birthtime", "mtime", "ctime", "oldest"):
        iso = filesystem_times.get(field)
        if not isinstance(iso, str):
            continue
        parsed = parse_datetime(iso)
        if parsed is None:
            continue
        normalized = with_local_timezone_if_naive(parsed)
        source = f"filesystem:{field}"
        key = (source, normalized.isoformat())
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            TimeEntry(source=source, timestamp=normalized, note=fs_note[field])
        )

    # Fallback: if no detailed time could be parsed, still expose best candidate lines.
    if not entries:
        for candidate in candidates:
            key = (candidate.source, candidate.iso)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                TimeEntry(
                    source=candidate.source,
                    timestamp=candidate.timestamp,
                    note=candidate.note,
                )
            )

    return sorted(entries, key=lambda e: e.timestamp.timestamp())


def format_time_map(values: dict[str, str], provider: str) -> dict[str, str]:
    formatted: dict[str, str] = {}
    for key, iso in values.items():
        parsed = parse_datetime(iso)
        if parsed is None:
            continue
        normalized = with_local_timezone_if_naive(parsed)
        source = f"{provider}:{key}"
        formatted[key] = format_output_time_for_source(source, normalized)
    return formatted


def format_optional_time_map(values: dict[str, str | None]) -> dict[str, str | None]:
    formatted: dict[str, str | None] = {}
    for key, iso in values.items():
        if iso is None:
            formatted[key] = None
            continue
        parsed = parse_datetime(iso)
        if parsed is None:
            formatted[key] = None
            continue
        formatted[key] = format_output_time(with_local_timezone_if_naive(parsed))
    return formatted


def main() -> int:
    args = parse_args()
    file_path = Path(args.file).expanduser()

    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1
    if not file_path.is_file():
        print(f"Error: not a file: {file_path}", file=sys.stderr)
        return 1

    try:
        best, all_candidates, metadata_times, filesystem_times = estimate_creation_time(file_path)
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    sorted_times = collect_all_times(all_candidates, metadata_times, filesystem_times)
    formatted_metadata_times = {
        provider: format_time_map(times, provider) for provider, times in metadata_times.items()
    }
    formatted_filesystem_times = format_optional_time_map(filesystem_times)

    payload = {
        "file": os.fspath(file_path),
        "most_likely_creation_time": format_output_time(best.timestamp),
        "confidence": confidence_label(best.score),
        "source": best.source,
        "note": best.note,
        "sorted_times": [
            {
                "source": entry.source,
                "time": format_output_time_for_source(entry.source, entry.timestamp),
                "note": entry.note,
            }
            for entry in sorted_times
        ],
        "metadata_times": formatted_metadata_times,
        "filesystem_times": formatted_filesystem_times,
        "candidates": [
            {
                "source": candidate.source,
                "time": format_output_time_for_source(candidate.source, candidate.timestamp),
                "confidence_score": round(candidate.score, 3),
                "note": candidate.note,
            }
            for candidate in all_candidates
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"file: {payload['file']}")
        print(f"most_likely_creation_time: {payload['most_likely_creation_time']}")
        print(f"confidence: {payload['confidence']}")
        print(f"source: {payload['source']}")
        print(f"note: {payload['note']}")
        print("")
        print("times_oldest_to_newest:")
        for entry in sorted_times:
            print(
                f"{format_output_time_for_source(entry.source, entry.timestamp)} | "
                f"{entry.source} | {entry.note}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
