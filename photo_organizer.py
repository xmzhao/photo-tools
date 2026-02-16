#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from common.file_datetime import collect_file_datetime_context
from common.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

SIDECAR_EXTENSIONS = {
    ".aae",
    ".xmp",
}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS


@dataclass(frozen=True)
class TimeEstimate:
    estimated_at: datetime
    media_most_likely_at: datetime | None
    fs_most_likely_at: datetime


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
        choices=["dry_run", "copy", "move"],
        default="dry_run",
        help="Transfer mode: dry_run (default), copy files, or move files.",
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


def estimate_time(file_path: Path) -> TimeEstimate:
    suffix = file_path.suffix.lower()
    context = collect_file_datetime_context(
        file_path,
        allow_nonzero_tool_exit=True,
        include_ffprobe_for_unknown=suffix not in SIDECAR_EXTENSIONS,
    )

    if context.most_likely < 0 or context.most_likely >= len(context.candidates):
        raise RuntimeError("invalid pair most_likely index")
    if context.fs_most_likely < 0 or context.fs_most_likely >= len(context.candidates):
        raise RuntimeError("invalid fs most_likely index")

    best = context.candidates[context.most_likely]
    fs_best = context.candidates[context.fs_most_likely]
    media_best = (
        context.candidates[context.media_most_likely]
        if context.media_most_likely >= 0
        else None
    )

    return TimeEstimate(
        estimated_at=best.timestamp,
        media_most_likely_at=media_best.timestamp if media_best else None,
        fs_most_likely_at=fs_best.timestamp,
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

        if mode != "dry_run":
            quarter_dir.mkdir(parents=True, exist_ok=True)

        existing_date_dirs = (
            sorted(p for p in quarter_dir.iterdir() if p.is_dir() and p.name.startswith(record.date_key))
            if quarter_dir.exists()
            else []
        )
        target_dir = existing_date_dirs[0] if existing_date_dirs else quarter_dir / record.date_key
        if mode != "dry_run":
            target_dir.mkdir(parents=True, exist_ok=True)
        date_folders.add(record.date_key)

        target_file = target_dir / record.source.name
        if target_file.exists() or target_file in dest_source_map:
            skipped += 1
            from_source = dest_source_map.get(target_file)
            skipped_entries.append((record, target_file, from_source))
            continue

        if mode == "move":
            shutil.move(record.source, target_file)
        elif mode == "copy":
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
            "source\testimated_at\tmedia_most_likely_at\tfs_most_likely_at\ttarget\tfrom_source\n"
        )
        for record, target, from_source in skipped_entries:
            from_source_text = str(from_source) if from_source else "unknown"
            f.write(
                f"{record.source}\t"
                f"{format_dt(record.estimate.estimated_at)}\t"
                f"{format_dt(record.estimate.media_most_likely_at)}\t"
                f"{format_dt(record.estimate.fs_most_likely_at)}\t"
                f"{target}\t"
                f"{from_source_text}\n"
            )
    return log_path


def write_copied_log(copied_entries: list[tuple[FileRecord, Path]]) -> Path:
    log_path = Path.cwd() / "copied_files.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write(
            "source\testimated_at\tmedia_most_likely_at\tfs_most_likely_at\ttarget\n"
        )
        for record, target in copied_entries:
            f.write(
                f"{record.source}\t"
                f"{format_dt(record.estimate.estimated_at)}\t"
                f"{format_dt(record.estimate.media_most_likely_at)}\t"
                f"{format_dt(record.estimate.fs_most_likely_at)}\t"
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

    if args.mode != "dry_run":
        output_dir.mkdir(parents=True, exist_ok=True)

    records, processed = collect_plan(input_dir)
    copied, skipped, date_folder_count, skipped_entries, copied_entries = copy_by_plan(
        records, output_dir, args.mode
    )
    skipped_log_path = write_skipped_log(skipped_entries)
    copied_log_path = write_copied_log(copied_entries)

    action_label = "Moved" if args.mode == "move" else ("Copied" if args.mode == "copy" else "Planned")
    print(
        f"Done. Processed: {processed}, {action_label}: {copied}, "
        f"Date folders: {date_folder_count}, Output: {output_dir}, "
        f"Skipped: {skipped}, Mode: {args.mode}, "
        f"Skip Log: {skipped_log_path}, Copied Log: {copied_log_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
