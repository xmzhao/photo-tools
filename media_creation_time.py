#!/usr/bin/env python3
"""Estimate the most likely datetime for an image or video file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from common.file_datetime import (
    FileDatetimeContext,
    TimeCandidate,
    collect_file_datetime_context,
    infer_path_precision,
    sort_candidates,
)


def format_output_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_output_time_for_source(source: str, value: datetime) -> str:
    precision = infer_path_precision(source)
    if precision == "year":
        return value.strftime("%Y")
    if precision == "month":
        return value.strftime("%Y-%m")
    if precision == "date":
        return value.strftime("%Y-%m-%d")
    return format_output_time(value)


def format_candidate_time(candidate: TimeCandidate) -> str:
    source_for_precision = candidate.origin_source or candidate.source
    return format_output_time_for_source(source_for_precision, candidate.timestamp)


def display_source(candidate: TimeCandidate) -> str:
    return candidate.source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return the most likely datetime for an image or video file."
    )
    parser.add_argument("file", help="Image/video file path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    return parser.parse_args()


def collect_all_times(candidates: list[TimeCandidate]) -> list[TimeCandidate]:
    entries: list[TimeCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.source, candidate.iso)
        if key in seen:
            continue
        seen.add(key)
        entries.append(candidate)
    return sort_candidates(entries)


def build_media_times(candidates: list[TimeCandidate]) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {"exiftool": {}, "ffprobe": {}}
    for candidate in candidates:
        if candidate.source.startswith("exiftool:"):
            key = candidate.source.split(":", 1)[1]
            values["exiftool"][key] = format_candidate_time(candidate)
        elif candidate.source.startswith("ffprobe:"):
            key = candidate.source.split(":", 1)[1]
            values["ffprobe"][key] = format_candidate_time(candidate)
    return values


def build_fs_times(candidates: list[TimeCandidate]) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "birthtime": None,
        "mtime": None,
        "ctime": None,
        "path": None,
    }
    for candidate in candidates:
        if candidate.source == "fs:birthtime":
            values["birthtime"] = format_candidate_time(candidate)
        elif candidate.source == "fs:mtime":
            values["mtime"] = format_candidate_time(candidate)
        elif candidate.source == "fs:ctime":
            values["ctime"] = format_candidate_time(candidate)
        elif candidate.source == "fs:path":
            values["path"] = format_candidate_time(candidate)
    return values


def candidate_payload(candidate: TimeCandidate, from_source: str | None = None) -> dict[str, str]:
    payload = {
        "source": display_source(candidate),
        "time": format_candidate_time(candidate),
        "note": candidate.note,
    }
    if from_source:
        payload["from"] = from_source
    return payload


def candidate_at(candidates: list[TimeCandidate], index: int) -> TimeCandidate | None:
    if index < 0 or index >= len(candidates):
        return None
    return candidates[index]


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
        context: FileDatetimeContext = collect_file_datetime_context(
            file_path,
            allow_nonzero_tool_exit=False,
            include_ffprobe_for_unknown=True,
        )
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    sorted_times = collect_all_times(context.candidates)

    pair_most_likely = candidate_at(context.candidates, context.most_likely)
    fs_most_likely = candidate_at(context.candidates, context.fs_most_likely)
    media_most_likely = candidate_at(context.candidates, context.media_most_likely)
    if pair_most_likely is None or fs_most_likely is None:
        print("Error: invalid most_likely indexes in context", file=sys.stderr)
        return 1

    payload = {
        "file": os.fspath(file_path),
        "most_likely_creation_time": format_candidate_time(pair_most_likely),
        "source": display_source(pair_most_likely),
        "note": pair_most_likely.note,
        "fs_most_likely": context.fs_most_likely,
        "media_most_likely": context.media_most_likely,
        "pair_most_likely": context.most_likely,
        "fs_most_likely_candidate": candidate_payload(
            fs_most_likely,
            from_source=fs_most_likely.origin_source or fs_most_likely.source,
        ),
        "media_most_likely_candidate": (
            candidate_payload(
                media_most_likely,
                from_source=(
                    media_most_likely.origin_source or media_most_likely.source
                    if media_most_likely is not None
                    else None
                ),
            )
            if media_most_likely is not None
            else None
        ),
        "pair_most_likely_candidate": candidate_payload(
            pair_most_likely,
            from_source=pair_most_likely.origin_source or pair_most_likely.source,
        ),
        "sorted_times": [
            {
                "source": display_source(entry),
                "time": format_candidate_time(entry),
                "note": entry.note,
            }
            for entry in sorted_times
        ],
        "media_times": build_media_times(context.candidates),
        "fs_times": build_fs_times(context.candidates),
        "candidates": [
            candidate_payload(candidate)
            for candidate in sort_candidates(context.candidates)
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"file: {payload['file']}")
        print(f"most_likely_creation_time: {payload['most_likely_creation_time']}")
        print(f"source: {payload['source']}")
        print(f"note: {payload['note']}")
        print("")
        print("times_ranked:")
        for entry in sorted_times:
            print(f"{format_candidate_time(entry)} | {display_source(entry)} | {entry.note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
