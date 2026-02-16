"""Shared file datetime extraction helpers."""

from __future__ import annotations

import os
import re
from functools import cmp_to_key
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.datetime_utils import parse_datetime, with_local_timezone_if_naive
from common.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from common.process import run_json_command


@dataclass(frozen=True)
class TimeCandidate:
    """单个时间候选项。

    字段说明:
    - source: 候选来源标识。常见值:
      - `exiftool:DateTimeOriginal`
      - `ffprobe:format.tags.creation_time`
      - `fs:birthtime` / `fs:mtime` / `fs:ctime` / `fs:path`
    - timestamp: 候选时间值，统一为带本地时区的 `datetime`。
    - note: 人类可读的来源说明，用于 CLI/JSON 输出解释。
    - origin_source: 真实来源标识（可选）。
      - 当 `source` 是聚合候选（例如 `fs:path`）时，
        该字段保存其回溯来源（例如 `path:parent:YYYYMMDD`）。
      - 用于按路径精度格式化展示（年/月/日/完整时间）。
    """

    # 候选来源（算法/元数据字段/文件系统字段）
    source: str
    # 标准化后的候选时间值（本地时区）
    timestamp: datetime
    # 解释性文本（用于输出）
    note: str
    # 聚合候选的真实来源；普通候选为 None
    origin_source: str | None = None

    @property
    def iso(self) -> str:
        return self.timestamp.isoformat()


@dataclass(frozen=True)
class FileDatetimeContext:
    """文件时间分析上下文。

    字段说明:
    - candidates: 全量候选数组，包含 fs/media 等所有候选。
      后续 `*_most_likely` 字段都通过“数组下标”引用本数组。
    - fs_most_likely: `candidates` 中“文件系统维度最可能时间”的下标。
      -1 表示不存在（理论上 fs 候选始终存在，因此通常不会为 -1）。
    - media_most_likely: `candidates` 中“媒体元数据维度最可能时间”的下标。
      当没有 exiftool/ffprobe 可用候选时为 -1。
    - most_likely: `candidates` 中“最终最可能时间（fs 与 media 融合后）”的下标。
      -1 表示不存在（正常流程不会出现）。
    """

    # 全量候选集合；索引型字段均引用该数组
    candidates: list[TimeCandidate]
    # 文件系统维度 most-likely 在 candidates 中的下标（-1 表示不存在）
    fs_most_likely: int
    # 媒体维度 most-likely 在 candidates 中的下标（-1 表示不存在）
    media_most_likely: int
    # 全局融合后的 most-likely 在 candidates 中的下标（-1 表示不存在）
    most_likely: int


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


def _build_datetime(
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


def _candidate_from(candidate: TimeCandidate) -> str:
    return candidate.origin_source or candidate.source


def _precision_digits(candidate: TimeCandidate) -> int:
    source = _candidate_from(candidate)
    precision = infer_path_precision(source)
    if precision == "year":
        return 4
    if precision == "month":
        return 6
    if precision == "date":
        return 8
    if precision == "datetime":
        return 17
    return 17


def _timestamp_token(value: datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S") + f"{value.microsecond // 1000:03d}"


def _precision_token(candidate: TimeCandidate) -> str:
    digits = _precision_digits(candidate)
    return _timestamp_token(candidate.timestamp)[:digits]


def _has_prefix_relation(a: TimeCandidate, b: TimeCandidate) -> bool:
    ta = _precision_token(a)
    tb = _precision_token(b)
    if len(ta) == len(tb):
        return ta == tb
    if len(ta) < len(tb):
        return tb.startswith(ta)
    return ta.startswith(tb)


def _compare_candidate(a: TimeCandidate, b: TimeCandidate) -> int:
    da = _precision_digits(a)
    db = _precision_digits(b)
    if _has_prefix_relation(a, b) and da != db:
        return -1 if da > db else 1

    ta = a.timestamp.timestamp()
    tb = b.timestamp.timestamp()
    if ta < tb:
        return -1
    if ta > tb:
        return 1

    if da != db:
        return -1 if da > db else 1
    sa = _candidate_from(a)
    sb = _candidate_from(b)
    if sa < sb:
        return -1
    if sa > sb:
        return 1
    return 0


def choose_most_likely(candidates: list[TimeCandidate]) -> TimeCandidate:
    if not candidates:
        raise RuntimeError("No datetime candidates available")
    ranked = sorted(candidates, key=cmp_to_key(_compare_candidate))
    return ranked[0]


def sort_candidates(candidates: list[TimeCandidate]) -> list[TimeCandidate]:
    return sorted(candidates, key=cmp_to_key(_compare_candidate))


def exiftool_candidates(
    file_path: Path,
    *,
    require_success: bool,
) -> list[TimeCandidate]:
    payload = run_json_command(
        ["exiftool", "-j", "-s", "-n", str(file_path)],
        require_success=require_success,
    )
    if not isinstance(payload, list) or not payload:
        return []

    record = payload[0]
    if not isinstance(record, dict):
        return []

    candidates: list[TimeCandidate] = []
    priority = [
        ("DateTimeOriginal", "EXIF capture datetime"),
        ("CreateDate", "Embedded create datetime"),
        ("DateTimeDigitized", "Digitized datetime"),
        ("CreationDate", "Container create datetime"),
        ("TrackCreateDate", "Video track create datetime"),
        ("MediaCreateDate", "Video media create datetime"),
        ("ModifyDate", "Embedded modify datetime"),
    ]

    for field, note in priority:
        raw = record.get(field)
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        normalized = with_local_timezone_if_naive(parsed)
        candidates.append(
            TimeCandidate(
                source=f"exiftool:{field}",
                timestamp=normalized,
                note=note,
            )
        )

    return candidates


def ffprobe_candidates(
    file_path: Path,
    *,
    require_success: bool,
) -> list[TimeCandidate]:
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
        ],
        require_success=require_success,
    )
    if not isinstance(payload, dict):
        return []

    candidates: list[TimeCandidate] = []
    format_tags = payload.get("format", {}).get("tags", {})
    if isinstance(format_tags, dict):
        raw = format_tags.get("creation_time")
        parsed = parse_datetime(raw)
        if parsed is not None:
            normalized = with_local_timezone_if_naive(parsed)
            candidates.append(
                TimeCandidate(
                    source="ffprobe:format.tags.creation_time",
                    timestamp=normalized,
                    note="ffprobe container creation_time",
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
            normalized = with_local_timezone_if_naive(parsed)
            candidates.append(
                TimeCandidate(
                    source=f"ffprobe:streams[{idx}].tags.creation_time",
                    timestamp=normalized,
                    note="ffprobe stream creation_time",
                )
            )

    return candidates


def path_inferred_candidates(file_path: Path) -> list[TimeCandidate]:
    entries: list[TimeCandidate] = []

    scopes = [
        ("filename", file_path.stem),
        ("basename", file_path.name),
        ("parent", file_path.parent.name),
        ("fullpath", os.fspath(file_path)),
    ]

    datetime_patterns = [
        (
            "YYYYMMDD_HHMMSS",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})[._\- ]?(0[1-9]|1[0-2])[._\- ]?"
                r"(0[1-9]|[12]\d|3[01])[T _\-]?"
                r"([01]\d|2[0-3])[._\- ]?([0-5]\d)[._\- ]?([0-5]\d)(?!\d)"
            ),
        ),
        (
            "YYYY-MM-DD HH:MM:SS",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])[日]?[T _\-]?"
                r"([01]?\d|2[0-3])[:._\-时](0?[0-9]|[1-5]\d)[:._\-分]"
                r"(0?[0-9]|[1-5]\d)(?:秒)?(?!\d)"
            ),
        ),
    ]

    date_patterns = [
        (
            "YYYYMMDD",
            re.compile(
                r"(?<!\d)(19\d{2}|20\d{2})(0[1-9]|1[0-2])"
                r"(0[1-9]|[12]\d|3[01])(?!\d)"
            ),
        ),
        (
            "YYYY-MM-DD",
            re.compile(
                r"(?<!\d)(19\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])(?:日)?(?!\d)|"
                r"(?<!\d)(20\d{2})[./_\-年](0?[1-9]|1[0-2])[./_\-月]"
                r"(0?[1-9]|[12]\d|3[01])(?:日)?(?!\d)"
            ),
        ),
    ]

    month_patterns = [
        (
            "YYYYMM",
            re.compile(r"(?<!\d)(19\d{2}|20\d{2})[./_\- ]?(0[1-9]|1[0-2])(?!\d)"),
        ),
        (
            "YYYY-MM",
            re.compile(r"(?<!\d)(19\d{2}|20\d{2})[./_\-年](0?[1-9]|1[0-2])(?:月)?(?!\d)"),
        ),
    ]

    year_pattern = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

    def push(source: str, timestamp: datetime, note: str) -> None:
        entries.append(TimeCandidate(source=source, timestamp=timestamp, note=note))

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

    def precision_key(candidate: TimeCandidate) -> tuple[int, int, int, int, int, int, int]:
        precision = path_precision_level(candidate.source)
        ts = candidate.timestamp
        if precision == 1:
            return (1, ts.year, 0, 0, 0, 0, 0)
        if precision == 2:
            return (2, ts.year, ts.month, 0, 0, 0, 0)
        if precision == 3:
            return (3, ts.year, ts.month, ts.day, 0, 0, 0)
        return (4, ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)

    def same_timeline_coarser(coarse: TimeCandidate, fine: TimeCandidate) -> bool:
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

    for scope_name, text in scopes:
        if not text:
            continue

        for label, pattern in datetime_patterns:
            for match in pattern.finditer(text):
                groups = [g for g in match.groups() if g is not None]
                if len(groups) < 6:
                    continue
                year, month, day, hour, minute, second = map(int, groups[:6])
                parsed = _build_datetime(year, month, day, hour, minute, second)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Path inferred by {label} on {scope_name}"
                push(source=source, timestamp=parsed, note=note)

        for label, pattern in date_patterns:
            for match in pattern.finditer(text):
                groups = [g for g in match.groups() if g is not None]
                if len(groups) < 3:
                    continue
                year, month, day = map(int, groups[:3])
                parsed = _build_datetime(year, month, day)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Path inferred by {label} on {scope_name}"
                push(source=source, timestamp=parsed, note=note)

        for label, pattern in month_patterns:
            for match in pattern.finditer(text):
                year, month = map(int, match.groups()[:2])
                parsed = _build_datetime(year, month, 1)
                if parsed is None:
                    continue
                source = f"path:{scope_name}:{label}"
                note = f"Path inferred by {label} on {scope_name}"
                push(source=source, timestamp=parsed, note=note)

        for match in year_pattern.finditer(text):
            year = int(match.group(1))
            parsed = _build_datetime(year, 1, 1)
            if parsed is None:
                continue
            source = f"path:{scope_name}:YYYY"
            note = f"Path inferred by YYYY on {scope_name}"
            push(source=source, timestamp=parsed, note=note)

    by_result: dict[tuple[int, int, int, int, int, int, int], TimeCandidate] = {}
    for candidate in entries:
        key = precision_key(candidate)
        if key not in by_result:
            by_result[key] = candidate
    unique_results = list(by_result.values())

    filtered: list[TimeCandidate] = []
    for candidate in unique_results:
        if any(
            other is not candidate and same_timeline_coarser(candidate, other)
            for other in unique_results
        ):
            continue
        filtered.append(candidate)

    return sorted(filtered, key=lambda c: c.timestamp.timestamp())


def fs_candidates(file_path: Path) -> tuple[list[TimeCandidate], TimeCandidate]:
    stat = file_path.stat()
    birth_time = (
        datetime.fromtimestamp(stat.st_birthtime).astimezone()
        if hasattr(stat, "st_birthtime")
        else None
    )
    mtime = datetime.fromtimestamp(stat.st_mtime).astimezone()
    ctime = datetime.fromtimestamp(stat.st_ctime).astimezone()

    candidates: list[TimeCandidate] = []
    if birth_time is not None:
        candidates.append(
            TimeCandidate(
                source="fs:birthtime",
                timestamp=birth_time,
                note="fs birthtime",
            )
        )
    candidates.append(TimeCandidate(source="fs:mtime", timestamp=mtime, note="fs mtime"))
    candidates.append(TimeCandidate(source="fs:ctime", timestamp=ctime, note="fs ctime"))

    inferred_path_candidates = path_inferred_candidates(file_path)
    if inferred_path_candidates:
        path_best = choose_most_likely(inferred_path_candidates)
        candidates.append(
            TimeCandidate(
                source="fs:path",
                timestamp=path_best.timestamp,
                note="fs path inferred datetime",
                origin_source=path_best.source,
            )
        )

    most_likely = choose_most_likely(candidates)
    return candidates, most_likely


def collect_file_datetime_context(
    file_path: Path,
    *,
    allow_nonzero_tool_exit: bool,
    include_ffprobe_for_unknown: bool,
) -> FileDatetimeContext:
    suffix = file_path.suffix.lower()
    is_video = suffix in VIDEO_EXTENSIONS
    is_image = suffix in IMAGE_EXTENSIONS
    require_success = not allow_nonzero_tool_exit

    media_candidates: list[TimeCandidate] = []
    media_candidates.extend(
        exiftool_candidates(
            file_path,
            require_success=require_success,
        )
    )
    if is_video or (include_ffprobe_for_unknown and not is_image and not is_video):
        media_candidates.extend(
            ffprobe_candidates(
                file_path,
                require_success=require_success,
            )
        )

    media_most_likely: TimeCandidate | None = None
    if media_candidates:
        media_most_likely = choose_most_likely(media_candidates)

    fs_all, fs_most_likely = fs_candidates(file_path)

    pair: list[TimeCandidate] = [fs_most_likely]
    if media_most_likely is not None:
        pair.append(media_most_likely)
    most_likely = choose_most_likely(pair)

    candidates = media_candidates + fs_all
    fs_most_likely_idx = candidates.index(fs_most_likely)
    media_most_likely_idx = candidates.index(media_most_likely) if media_most_likely is not None else -1
    most_likely_idx = candidates.index(most_likely)

    return FileDatetimeContext(
        candidates=candidates,
        fs_most_likely=fs_most_likely_idx,
        media_most_likely=media_most_likely_idx,
        most_likely=most_likely_idx,
    )
