#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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

SIDECAR_EXTENSIONS = {
    ".aae",
    ".xmp",
}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS


@dataclass(frozen=True)
class Candidate:
    source: str
    timestamp: datetime
    score: float

    @property
    def iso(self) -> str:
        return self.timestamp.isoformat()


@dataclass(frozen=True)
class TimeEstimate:
    estimated_at: datetime
    estimated_source: str
    media_estimated_at: datetime | None
    filesystem_estimated_at: datetime
    filesystem_estimated_from: str


@dataclass(frozen=True)
class FileRecord:
    source: Path
    date_key: str
    estimate: TimeEstimate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Organize photos/videos/sidecar files by estimated date into output folders."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Root directory containing photos/videos/sidecar files (recursive).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Root directory to place organized files.",
    )
    parser.add_argument(
        "--mode",
        choices=["copy", "move"],
        default="copy",
        help="Transfer mode: copy files (default) or move files.",
    )
    return parser.parse_args()


def ensure_required_commands() -> bool:
    required = ["exiftool", "ffprobe"]
    missing = [cmd for cmd in required if shutil.which(cmd) is None]
    if not missing:
        return True

    print(
        "Error: missing required command(s): "
        + ", ".join(missing)
        + ". Please install them and retry.",
        file=sys.stderr,
    )
    return False


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


def run_json_command(command: list[str]) -> Any | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def exiftool_candidates(file_path: Path, is_video: bool) -> list[Candidate]:
    payload = run_json_command(["exiftool", "-j", "-s", "-n", str(file_path)])
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return []

    record = payload[0]
    candidates: list[Candidate] = []
    priority = [
        ("DateTimeOriginal", 1.00),
        ("CreateDate", 0.95),
        ("DateTimeDigitized", 0.92),
        ("CreationDate", 0.92),
        ("TrackCreateDate", 0.90),
        ("MediaCreateDate", 0.90),
        ("ModifyDate", 0.70),
    ]

    for field, score in priority:
        raw = record.get(field)
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        normalized = with_local_timezone_if_naive(parsed)
        candidates.append(
            Candidate(
                source=f"exiftool:{field}",
                timestamp=normalized,
                score=score if not is_video else min(score, 0.97),
            )
        )
    return candidates


def ffprobe_candidates(file_path: Path) -> list[Candidate]:
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
        return []

    candidates: list[Candidate] = []
    format_tags = payload.get("format", {}).get("tags", {})
    if isinstance(format_tags, dict):
        raw = format_tags.get("creation_time")
        parsed = parse_datetime(raw)
        if parsed is not None:
            candidates.append(
                Candidate(
                    source="ffprobe:format.tags.creation_time",
                    timestamp=with_local_timezone_if_naive(parsed),
                    score=0.93,
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
            parsed = parse_datetime(raw)
            if parsed is None:
                continue
            candidates.append(
                Candidate(
                    source=f"ffprobe:streams[{idx}].tags.creation_time",
                    timestamp=with_local_timezone_if_naive(parsed),
                    score=0.90,
                )
            )

    return candidates


def filesystem_candidate(file_path: Path) -> tuple[Candidate, str]:
    stat = file_path.stat()
    birth_time = (
        datetime.fromtimestamp(stat.st_birthtime).astimezone()
        if hasattr(stat, "st_birthtime")
        else None
    )
    mtime = datetime.fromtimestamp(stat.st_mtime).astimezone()
    ctime = datetime.fromtimestamp(stat.st_ctime).astimezone()
    named_times: list[tuple[str, datetime]] = [("mtime", mtime), ("ctime", ctime)]
    if birth_time is not None:
        named_times.append(("birthtime", birth_time))
    oldest_name, oldest_time = min(named_times, key=lambda item: item[1].timestamp())
    return (
        Candidate(
            source="filesystem:oldest_of_birth_mtime_ctime",
            timestamp=oldest_time,
            score=0.65,
        ),
        oldest_name,
    )


def estimate_time(file_path: Path) -> TimeEstimate:
    suffix = file_path.suffix.lower()
    is_video = suffix in VIDEO_EXTENSIONS
    is_image = suffix in IMAGE_EXTENSIONS

    all_candidates: list[Candidate] = []
    exif_candidates = exiftool_candidates(file_path, is_video=is_video)
    all_candidates.extend(exif_candidates)
    ffprobe_result: list[Candidate] = []

    if is_video or (not is_image and suffix not in SIDECAR_EXTENSIONS):
        ffprobe_result = ffprobe_candidates(file_path)
        all_candidates.extend(ffprobe_result)

    fs_candidate, fs_from = filesystem_candidate(file_path)
    all_candidates.append(fs_candidate)

    # Keep it simple: pick the oldest timestamp among all candidates.
    best = min(all_candidates, key=lambda c: c.timestamp.timestamp())
    media_candidates = exif_candidates + ffprobe_result
    media_best = (
        min(media_candidates, key=lambda c: c.timestamp.timestamp())
        if media_candidates
        else None
    )

    return TimeEstimate(
        estimated_at=best.timestamp,
        estimated_source=best.source,
        media_estimated_at=media_best.timestamp if media_best else None,
        filesystem_estimated_at=fs_candidate.timestamp,
        filesystem_estimated_from=fs_from,
    )


def get_date_key(estimate: TimeEstimate) -> str:
    return estimate.estimated_at.astimezone().strftime("%Y%m%d")


def iter_files(input_dir: Path) -> Iterable[Path]:
    for item in input_dir.rglob("*"):
        if not item.is_file():
            continue
        if item.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield item
            continue
        print(f"Ignored: {item}", file=sys.stderr)


def build_file_record(file_path: Path, forced_date_key: str | None = None) -> FileRecord:
    estimate = estimate_time(file_path)
    date_key = forced_date_key or get_date_key(estimate)
    return FileRecord(source=file_path, date_key=date_key, estimate=estimate)


def collect_plan(input_dir: Path) -> tuple[list[FileRecord], int]:
    files = sorted(iter_files(input_dir))
    records: list[FileRecord] = []
    stem_date_map: dict[tuple[Path, str], str] = {}

    for file_path in files:
        if file_path.suffix.lower() in SIDECAR_EXTENSIONS:
            continue
        record = build_file_record(file_path)
        records.append(record)
        stem_date_map[(file_path.parent, file_path.stem)] = record.date_key

    for file_path in files:
        if file_path.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        date_key = stem_date_map.get((file_path.parent, file_path.stem))
        records.append(build_file_record(file_path, forced_date_key=date_key))

    return records, len(files)


def copy_by_plan(
    records: list[FileRecord], output_dir: Path, mode: str
) -> tuple[
    int,
    int,
    int,
    list[tuple[FileRecord, Path, Path | None]],
    list[tuple[FileRecord, Path]],
]:
    copied = 0
    skipped = 0
    date_folders: set[str] = set()
    skipped_entries: list[tuple[FileRecord, Path, Path | None]] = []
    copied_entries: list[tuple[FileRecord, Path]] = []
    dest_source_map: dict[Path, Path] = {}

    for record in records:
        year = record.date_key[:4]
        month = int(record.date_key[4:6])
        quarter = ((month - 1) // 3) + 1
        quarter_key = f"{year}Q{quarter}"
        quarter_dir = output_dir / quarter_key
        quarter_dir.mkdir(parents=True, exist_ok=True)

        existing_date_dirs = sorted(
            p for p in quarter_dir.iterdir() if p.is_dir() and p.name.startswith(record.date_key)
        )
        target_dir = existing_date_dirs[0] if existing_date_dirs else quarter_dir / record.date_key
        target_dir.mkdir(parents=True, exist_ok=True)
        date_folders.add(record.date_key)

        target_file = target_dir / record.source.name
        if target_file.exists():
            skipped += 1
            from_source = dest_source_map.get(target_file)
            skipped_entries.append((record, target_file, from_source))
            continue

        if mode == "move":
            shutil.move(record.source, target_file)
        else:
            shutil.copy2(record.source, target_file)
        dest_source_map[target_file] = record.source
        copied_entries.append((record, target_file))
        copied += 1

    return copied, skipped, len(date_folders), skipped_entries, copied_entries


def format_dt(value: datetime | None) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value is not None else ""


def write_skipped_log(skipped_entries: list[tuple[FileRecord, Path, Path | None]]) -> Path:
    log_path = Path.cwd() / "skipped_files.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write(
            "source\testimated_at\testimated_source\texiftool|ffprobe_estimated_at\tfilesystem_estimated_at\tfilesystem_estimated_from\ttarget\tfrom_src\n"
        )
        for record, target, from_source in skipped_entries:
            from_source_text = str(from_source) if from_source else "unknown"
            f.write(
                f"{record.source}\t"
                f"{format_dt(record.estimate.estimated_at)}\t"
                f"{record.estimate.estimated_source}\t"
                f"{format_dt(record.estimate.media_estimated_at)}\t"
                f"{format_dt(record.estimate.filesystem_estimated_at)}\t"
                f"{record.estimate.filesystem_estimated_from}\t"
                f"{target}\t"
                f"{from_source_text}\n"
            )
    return log_path


def write_copied_log(copied_entries: list[tuple[FileRecord, Path]]) -> Path:
    log_path = Path.cwd() / "copied_files.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write(
            "source\testimated_at\testimated_source\texiftool|ffprobe_estimated_at\tfilesystem_estimated_at\tfilesystem_estimated_from\ttarget\n"
        )
        for record, target in copied_entries:
            f.write(
                f"{record.source}\t"
                f"{format_dt(record.estimate.estimated_at)}\t"
                f"{record.estimate.estimated_source}\t"
                f"{format_dt(record.estimate.media_estimated_at)}\t"
                f"{format_dt(record.estimate.filesystem_estimated_at)}\t"
                f"{record.estimate.filesystem_estimated_from}\t"
                f"{target}\n"
            )
    return log_path


def main() -> int:
    args = parse_args()

    if not ensure_required_commands():
        return 1

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory does not exist or is not a directory: {input_dir}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    records, processed = collect_plan(input_dir)
    copied, skipped, date_folder_count, skipped_entries, copied_entries = copy_by_plan(
        records, output_dir, args.mode
    )
    skipped_log_path = write_skipped_log(skipped_entries)
    copied_log_path = write_copied_log(copied_entries)

    action_label = "Moved" if args.mode == "move" else "Copied"
    print(
        f"Done. Processed: {processed}, {action_label}: {copied}, "
        f"Date folders: {date_folder_count}, Output: {output_dir}, "
        f"Skipped: {skipped}, Mode: {args.mode}, "
        f"Skip Log: {skipped_log_path}, Copied Log: {copied_log_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
