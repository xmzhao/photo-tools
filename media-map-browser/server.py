#!/usr/bin/env python3
"""Local-first media map browser server.

Run locally and open http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Make shared modules importable when this script runs from its own folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.file_datetime import collect_file_datetime_context
from common.gps import extract_gps
from common.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CACHE_DIR = APP_DIR / ".cache"
THUMB_DIR = CACHE_DIR / "thumbs"
PREVIEW_DIR = CACHE_DIR / "previews"
SCAN_CACHE_DIR = CACHE_DIR / "scans"
BOUNDARY_CACHE_DIR = CACHE_DIR / "boundaries"
META_CACHE_PATH = CACHE_DIR / "meta_cache.json"
SCAN_INDEX_PATH = CACHE_DIR / "scan_index.json"

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
BROWSER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"}

WORLD_BOUNDARY_URL = "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson"
CHINA_PROVINCE_BOUNDARY_URL = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
CHINA_PREFECTURE_CACHE_SCHEMA_VERSION = 2

SVG_VIDEO_PLACEHOLDER = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='240' viewBox='0 0 320 240'>"
    "<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>"
    "<stop offset='0%' stop-color='#7ea28a'/><stop offset='100%' stop-color='#3b4f56'/>"
    "</linearGradient></defs>"
    "<rect width='320' height='240' fill='url(#g)'/>"
    "<circle cx='92' cy='120' r='30' fill='rgba(255,255,255,0.2)'/>"
    "<polygon points='86,101 130,120 86,139' fill='white'/>"
    "<text x='160' y='128' text-anchor='middle' font-size='24' fill='white' opacity='0.85'>VIDEO</text>"
    "</svg>"
).encode("utf-8")

SVG_IMAGE_PLACEHOLDER = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='240' viewBox='0 0 320 240'>"
    "<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>"
    "<stop offset='0%' stop-color='#9dbcc8'/><stop offset='100%' stop-color='#5a6d8f'/>"
    "</linearGradient></defs>"
    "<rect width='320' height='240' fill='url(#g)'/>"
    "<rect x='60' y='52' rx='14' ry='14' width='200' height='136' fill='rgba(255,255,255,0.24)'/>"
    "<circle cx='110' cy='98' r='17' fill='rgba(255,255,255,0.56)'/>"
    "<polygon points='76,164 136,112 176,146 212,120 244,164' fill='rgba(255,255,255,0.65)'/>"
    "</svg>"
).encode("utf-8")


def now_ts() -> float:
    return time.time()


def iso_time(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def guess_media_type(file_path: Path) -> str | None:
    suffix = file_path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_cached_or_download(url: str, cache_file: Path, *, max_age_seconds: int = 30 * 24 * 3600) -> bytes:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists():
        age_seconds = now_ts() - cache_file.stat().st_mtime
        if age_seconds <= max_age_seconds:
            return cache_file.read_bytes()

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MediaMapBrowser/1.0 (+local tool)",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            data = response.read()
    except urllib.error.URLError as exc:
        if cache_file.exists():
            return cache_file.read_bytes()
        raise RuntimeError(f"download failed: {url}") from exc

    if not data:
        if cache_file.exists():
            return cache_file.read_bytes()
        raise RuntimeError(f"empty payload: {url}")
    cache_file.write_bytes(data)
    return data


def parse_geojson_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid geojson payload") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("invalid geojson object")
    return payload


def adcode_string(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:06d}"
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return text.zfill(6)
    return ""


def is_prefecture_level_adcode(code: str) -> bool:
    # Prefecture-level city adcode pattern: XXYY00 with YY != 00.
    if len(code) != 6 or not code.isdigit():
        return False
    return code[2:4] != "00" and code[4:6] == "00"


def build_china_prefecture_geojson() -> bytes:
    province_raw = read_cached_or_download(
        CHINA_PROVINCE_BOUNDARY_URL,
        BOUNDARY_CACHE_DIR / "china_provinces.geojson",
    )
    province_geo = parse_geojson_bytes(province_raw)
    province_features = province_geo.get("features")
    if not isinstance(province_features, list):
        raise RuntimeError("invalid province boundary data")

    # Use province polygon as a synthetic prefecture-level unit when upstream
    # data does not provide child city features (municipalities + SAR).
    direct_municipalities = {"110000", "120000", "310000", "500000", "710000", "810000", "820000"}
    seen_codes: set[str] = set()
    merged_features: list[dict[str, Any]] = []
    synthetic_sequence = 0

    def push_feature(feature: dict[str, Any], *, source: str) -> None:
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}
            feature["properties"] = props
        code = adcode_string(props.get("adcode"))
        if code and code in seen_codes:
            return
        if code:
            seen_codes.add(code)
        props["_source"] = source
        merged_features.append(feature)

    for province in province_features:
        if not isinstance(province, dict):
            continue
        props = province.get("properties")
        if not isinstance(props, dict):
            continue
        province_code = adcode_string(props.get("adcode"))
        if not province_code:
            continue

        found_prefecture = False
        # Prefer "_full" geometry first; fallback to non-full only when needed.
        for suffix in ("_full", ""):
            cache_path = BOUNDARY_CACHE_DIR / f"province_{province_code}{suffix}.geojson"
            url = f"https://geo.datav.aliyun.com/areas_v3/bound/{province_code}{suffix}.json"
            try:
                city_raw = read_cached_or_download(url, cache_path)
                city_geo = parse_geojson_bytes(city_raw)
            except RuntimeError:
                continue
            city_features = city_geo.get("features")
            if not isinstance(city_features, list):
                continue

            for city in city_features:
                if not isinstance(city, dict):
                    continue
                geometry = city.get("geometry")
                if not isinstance(geometry, dict):
                    continue
                city_props = city.get("properties")
                if not isinstance(city_props, dict):
                    continue
                city_code = adcode_string(city_props.get("adcode"))
                level = str(city_props.get("level", "")).lower()
                if is_prefecture_level_adcode(city_code) or level == "city":
                    cloned = {
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": dict(city_props),
                    }
                    push_feature(cloned, source=f"province:{province_code}")
                    found_prefecture = True

        if found_prefecture:
            continue

        # For direct municipalities, fallback to municipality boundary as one city-level region.
        if province_code in direct_municipalities:
            geometry = province.get("geometry")
            if isinstance(geometry, dict):
                synthetic_sequence += 1
                fallback_feature = {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": dict(props),
                }
                fallback_feature["properties"]["adcode"] = province_code
                fallback_feature["properties"]["level"] = "city"
                fallback_feature["properties"]["_synthetic_prefecture"] = True
                fallback_feature["properties"]["_synthetic_seq"] = synthetic_sequence
                push_feature(fallback_feature, source=f"synthetic:{province_code}")

    # Keep special national overlays (e.g., South China Sea nine-dash line)
    # consistent with province-mode data.
    for province in province_features:
        if not isinstance(province, dict):
            continue
        props = province.get("properties")
        if not isinstance(props, dict):
            continue
        code = str(props.get("adcode", "")).strip()
        if "_JD" not in code:
            continue
        geometry = province.get("geometry")
        if not isinstance(geometry, dict):
            continue
        special_feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": dict(props),
        }
        special_feature["properties"]["_synthetic_prefecture"] = True
        push_feature(special_feature, source="province:special")

    if not merged_features:
        raise RuntimeError("failed to build china prefecture geojson")

    payload = {
        "type": "FeatureCollection",
        "_meta": {
            "schema_version": CHINA_PREFECTURE_CACHE_SCHEMA_VERSION,
            "full_geometry_preferred": True,
        },
        "features": merged_features,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cache_path = BOUNDARY_CACHE_DIR / "china_prefecture_cities.geojson"
    cache_path.write_bytes(encoded)
    return encoded


def china_prefecture_cache_is_complete(raw: bytes) -> bool:
    try:
        payload = parse_geojson_bytes(raw)
    except RuntimeError:
        return False
    features = payload.get("features")
    if not isinstance(features, list):
        return False

    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return False
    version = meta.get("schema_version")
    try:
        version_num = int(version)
    except (TypeError, ValueError):
        return False
    if version_num < CHINA_PREFECTURE_CACHE_SCHEMA_VERSION:
        return False

    expected = {"710000", "810000", "820000", "100000_JD"}
    found: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        raw_code = props.get("adcode")
        if isinstance(raw_code, str):
            code = raw_code.strip()
        else:
            code = adcode_string(raw_code)
        if code in expected:
            found.add(code)
            if found == expected:
                return True
    return False


def count_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for _, _, files in os.walk(directory):
        total += len(files)
    return total


def list_media_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for root_str, _, filenames in os.walk(root, followlinks=False):
        current_root = Path(root_str)
        for name in filenames:
            file_path = current_root / name
            if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(file_path)
    files.sort(key=lambda p: str(p).lower())
    return files


def infer_capture_time(file_path: Path) -> str | None:
    try:
        context = collect_file_datetime_context(
            file_path,
            allow_nonzero_tool_exit=True,
            include_ffprobe_for_unknown=True,
        )
    except Exception:  # noqa: BLE001
        return None

    if context.most_likely < 0 or context.most_likely >= len(context.candidates):
        return None

    return context.candidates[context.most_likely].timestamp.isoformat()


def parse_range_header(header: str | None, file_size: int) -> tuple[int, int] | None:
    if not header:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)$", header.strip())
    if not match:
        return None

    start_str, end_str = match.groups()
    if start_str == "" and end_str == "":
        return None

    try:
        if start_str == "":
            length = int(end_str)
            if length <= 0:
                return None
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
    except ValueError:
        return None

    if start < 0 or end < start or start >= file_size:
        return None
    end = min(end, file_size - 1)
    return start, end


@dataclass
class MediaRecord:
    media_id: str
    path: str
    name: str
    media_type: str
    extension: str
    size: int
    mtime: float
    lat: float | None
    lon: float | None
    captured_at: str | None

    def to_item(self) -> dict[str, Any]:
        return {
            "id": self.media_id,
            "name": self.name,
            "path": self.path,
            "type": self.media_type,
            "extension": self.extension,
            "size": self.size,
            "mtime": self.mtime,
            "captured_at": self.captured_at,
            "lat": self.lat,
            "lon": self.lon,
        }


@dataclass
class ScanJob:
    job_id: str
    root_path: str
    status: str = "running"
    total: int = 0
    processed: int = 0
    located: int = 0
    unlocated: int = 0
    started_at: float = field(default_factory=now_ts)
    ended_at: float | None = None
    error: str | None = None
    records: list[MediaRecord] = field(default_factory=list)

    def to_status(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "root_path": self.root_path,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "located": self.located,
            "unlocated": self.unlocated,
            "started_at": iso_time(self.started_at),
            "ended_at": iso_time(self.ended_at),
            "error": self.error,
        }


class AppState:
    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        BOUNDARY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, ScanJob] = {}
        self.media_index: dict[str, MediaRecord] = {}
        raw_cache = load_json(META_CACHE_PATH, {})
        self.meta_cache: dict[str, dict[str, Any]] = raw_cache if isinstance(raw_cache, dict) else {}
        raw_index = load_json(SCAN_INDEX_PATH, [])
        self.scan_index: dict[str, dict[str, Any]] = {}
        if isinstance(raw_index, list):
            for entry in raw_index:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("scan_id"), str)
                    and isinstance(entry.get("root_path"), str)
                ):
                    self.scan_index[entry["scan_id"]] = entry
        self.meta_changed = False
        self.lock = threading.Lock()

    def flush_meta_cache(self) -> None:
        with self.lock:
            if not self.meta_changed:
                return
            payload = dict(self.meta_cache)
            self.meta_changed = False
        save_json(META_CACHE_PATH, payload)

    def save_scan_cache(self, root_path: str, records: list[MediaRecord]) -> None:
        scan_id = sha1_text(str(Path(root_path).expanduser().resolve()))
        located_items = [r.to_item() for r in records if r.lat is not None and r.lon is not None]
        unlocated_items = [r.to_item() for r in records if r.lat is None or r.lon is None]
        updated_at = iso_time(now_ts())
        cache_payload = {
            "scan_id": scan_id,
            "root_path": root_path,
            "updated_at": updated_at,
            "summary": {
                "total": len(records),
                "located": len(located_items),
                "unlocated": len(unlocated_items),
            },
            "items": located_items,
            "unlocated": unlocated_items,
        }
        save_json(SCAN_CACHE_DIR / f"{scan_id}.json", cache_payload)
        with self.lock:
            self.scan_index[scan_id] = {
                "scan_id": scan_id,
                "root_path": root_path,
                "updated_at": updated_at,
                "total": len(records),
                "located": len(located_items),
                "unlocated": len(unlocated_items),
            }
            entries = sorted(
                self.scan_index.values(),
                key=lambda x: str(x.get("updated_at") or ""),
                reverse=True,
            )
        save_json(SCAN_INDEX_PATH, entries)

    def _register_items(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            media_id = row.get("id")
            path = row.get("path")
            media_type = row.get("type")
            if not isinstance(media_id, str) or not isinstance(path, str) or not isinstance(media_type, str):
                continue
            try:
                size = int(row.get("size", 0))
                mtime = float(row.get("mtime", 0))
            except (TypeError, ValueError):
                continue
            record = MediaRecord(
                media_id=media_id,
                path=path,
                name=str(row.get("name", Path(path).name)),
                media_type=media_type,
                extension=str(row.get("extension", Path(path).suffix.lower())),
                size=size,
                mtime=mtime,
                lat=row.get("lat"),
                lon=row.get("lon"),
                captured_at=row.get("captured_at"),
            )
            self.media_index[media_id] = record

    def list_scan_caches(self) -> list[dict[str, Any]]:
        with self.lock:
            entries = list(self.scan_index.values())
        entries.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        return entries

    def load_scan_cache(self, scan_id: str) -> dict[str, Any] | None:
        cache_file = SCAN_CACHE_DIR / f"{scan_id}.json"
        payload = load_json(cache_file, None)
        if not isinstance(payload, dict):
            return None
        items = payload.get("items")
        unlocated = payload.get("unlocated")
        if not isinstance(items, list) or not isinstance(unlocated, list):
            return None
        with self.lock:
            self._register_items(items)
            self._register_items(unlocated)
        return payload

    def load_scan_caches(self, scan_ids: list[str]) -> tuple[dict[str, Any], list[str], list[str]]:
        unique_scan_ids: list[str] = []
        seen_ids: set[str] = set()
        for scan_id in scan_ids:
            normalized = str(scan_id).strip()
            if not normalized or normalized in seen_ids:
                continue
            seen_ids.add(normalized)
            unique_scan_ids.append(normalized)

        loaded_scan_ids: list[str] = []
        missing_scan_ids: list[str] = []
        merged_items: list[dict[str, Any]] = []
        merged_unlocated: list[dict[str, Any]] = []
        seen_media: set[str] = set()

        def media_key(item: dict[str, Any]) -> str:
            media_id = item.get("id")
            if isinstance(media_id, str) and media_id:
                return f"id:{media_id}"
            media_path = item.get("path")
            if isinstance(media_path, str) and media_path:
                return f"path:{media_path}"
            return f"fallback:{hashlib.sha1(json.dumps(item, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()}"

        for scan_id in unique_scan_ids:
            payload = self.load_scan_cache(scan_id)
            if payload is None:
                missing_scan_ids.append(scan_id)
                continue
            loaded_scan_ids.append(scan_id)

            items = payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    key = media_key(item)
                    if key in seen_media:
                        continue
                    seen_media.add(key)
                    merged_items.append(item)

            unlocated = payload.get("unlocated")
            if isinstance(unlocated, list):
                for item in unlocated:
                    if not isinstance(item, dict):
                        continue
                    key = media_key(item)
                    if key in seen_media:
                        continue
                    seen_media.add(key)
                    merged_unlocated.append(item)

        merged_payload = {
            "summary": {
                "total": len(merged_items) + len(merged_unlocated),
                "located": len(merged_items),
                "unlocated": len(merged_unlocated),
            },
            "items": merged_items,
            "unlocated": merged_unlocated,
            "loaded_scan_ids": loaded_scan_ids,
            "missing_scan_ids": missing_scan_ids,
        }
        return merged_payload, loaded_scan_ids, missing_scan_ids

    def delete_scan_cache(self, scan_id: str) -> bool:
        removed = False
        cache_file = SCAN_CACHE_DIR / f"{scan_id}.json"
        if cache_file.exists():
            cache_file.unlink()
            removed = True
        with self.lock:
            if scan_id in self.scan_index:
                self.scan_index.pop(scan_id, None)
                removed = True
            entries = sorted(
                self.scan_index.values(),
                key=lambda x: str(x.get("updated_at") or ""),
                reverse=True,
            )
        save_json(SCAN_INDEX_PATH, entries)
        return removed

    def clear_all_cache(self) -> dict[str, Any]:
        with self.lock:
            self.scan_index = {}
            self.meta_cache = {}
            self.meta_changed = False
            self.media_index = {}
        for folder in (THUMB_DIR, PREVIEW_DIR, SCAN_CACHE_DIR, BOUNDARY_CACHE_DIR):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        for file_path in (META_CACHE_PATH, SCAN_INDEX_PATH):
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
        return self.cache_stats()

    def cache_stats(self) -> dict[str, Any]:
        with self.lock:
            meta_entries = len(self.meta_cache)
            scan_entries = len(self.scan_index)
        return {
            "meta_entries": meta_entries,
            "scan_entries": scan_entries,
            "thumb_files": count_files(THUMB_DIR),
            "preview_files": count_files(PREVIEW_DIR),
            "scan_files": count_files(SCAN_CACHE_DIR),
            "boundary_files": count_files(BOUNDARY_CACHE_DIR),
        }

    def start_job(self, root_path: str) -> ScanJob:
        job = ScanJob(job_id=str(uuid.uuid4()), root_path=root_path)
        with self.lock:
            self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> ScanJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def get_media(self, media_id: str) -> MediaRecord | None:
        with self.lock:
            return self.media_index.get(media_id)

    def _get_cached_record(self, file_path: Path, stat: Any) -> MediaRecord | None:
        key = str(file_path)
        cache = self.meta_cache.get(key)
        if not cache:
            return None

        if cache.get("size") != stat.st_size or cache.get("mtime") != stat.st_mtime:
            return None

        return MediaRecord(
            media_id=cache["media_id"],
            path=key,
            name=cache["name"],
            media_type=cache["media_type"],
            extension=cache["extension"],
            size=cache["size"],
            mtime=cache["mtime"],
            lat=cache.get("lat"),
            lon=cache.get("lon"),
            captured_at=cache.get("captured_at"),
        )

    def _build_record(self, file_path: Path, stat: Any) -> MediaRecord:
        media_type = guess_media_type(file_path)
        if media_type is None:
            raise RuntimeError(f"Unsupported media file: {file_path}")

        gps = extract_gps(file_path, image_extensions=IMAGE_EXTENSIONS)
        captured_at = infer_capture_time(file_path)
        lat, lon = (gps if gps else (None, None))
        media_id = sha1_text(f"{file_path}|{stat.st_mtime}|{stat.st_size}")

        record = MediaRecord(
            media_id=media_id,
            path=str(file_path),
            name=file_path.name,
            media_type=media_type,
            extension=file_path.suffix.lower(),
            size=stat.st_size,
            mtime=stat.st_mtime,
            lat=lat,
            lon=lon,
            captured_at=captured_at,
        )

        return record

    def get_or_build_record(self, file_path: Path) -> MediaRecord:
        stat = file_path.stat()
        with self.lock:
            cached = self._get_cached_record(file_path, stat)
        if cached is not None:
            return cached

        record = self._build_record(file_path, stat)
        with self.lock:
            self.meta_cache[str(file_path)] = {
                "media_id": record.media_id,
                "name": record.name,
                "media_type": record.media_type,
                "extension": record.extension,
                "size": record.size,
                "mtime": record.mtime,
                "lat": record.lat,
                "lon": record.lon,
                "captured_at": record.captured_at,
            }
            self.meta_changed = True
        return record

    def update_job_progress(
        self,
        job: ScanJob,
        *,
        total: int | None = None,
        processed: int | None = None,
        located: int | None = None,
        unlocated: int | None = None,
    ) -> None:
        with self.lock:
            if total is not None:
                job.total = total
            if processed is not None:
                job.processed = processed
            if located is not None:
                job.located = located
            if unlocated is not None:
                job.unlocated = unlocated

    def complete_job(self, job: ScanJob, records: list[MediaRecord]) -> None:
        located = sum(1 for r in records if r.lat is not None and r.lon is not None)
        unlocated = len(records) - located
        with self.lock:
            job.records = records
            job.status = "completed"
            job.located = located
            job.unlocated = unlocated
            job.processed = len(records)
            job.ended_at = now_ts()
            for record in records:
                self.media_index[record.media_id] = record

    def fail_job(self, job: ScanJob, error: str) -> None:
        with self.lock:
            job.status = "failed"
            job.error = error
            job.ended_at = now_ts()


STATE = AppState()


def resampling_lanczos() -> int:
    if Image is None:
        return 1
    resampling = getattr(Image, "Resampling", Image)
    return int(getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1)))


def cache_key(record: MediaRecord, purpose: str) -> str:
    return sha1_text(f"{purpose}|{record.path}|{record.mtime}|{record.size}")


def placeholder_for(record: MediaRecord) -> bytes:
    if record.media_type == "video":
        return SVG_VIDEO_PLACEHOLDER
    return SVG_IMAGE_PLACEHOLDER


def save_square_jpeg(image_obj: Any, target: Path, size: int = 320) -> bool:
    if Image is None or ImageOps is None:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = ImageOps.exif_transpose(image_obj)
        fitted = ImageOps.fit(normalized.convert("RGB"), (size, size), method=resampling_lanczos())
        fitted.save(target, "JPEG", quality=84, optimize=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def save_preview_jpeg(image_obj: Any, target: Path, max_edge: int = 1920) -> bool:
    if Image is None:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = image_obj
        if ImageOps is not None:
            normalized = ImageOps.exif_transpose(normalized)
        img = normalized.convert("RGB")
        img.thumbnail((max_edge, max_edge), resample=resampling_lanczos())
        img.save(target, "JPEG", quality=88, optimize=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def create_image_derivative(
    source: Path,
    target: Path,
    *,
    square: bool,
    size: int = 320,
    max_edge: int = 1920,
) -> bool:
    if Image is not None:
        try:
            with Image.open(source) as img:
                if square:
                    if save_square_jpeg(img, target, size=size):
                        return True
                else:
                    if save_preview_jpeg(img, target, max_edge=max_edge):
                        return True
        except Exception:  # noqa: BLE001
            pass

    # Try embedded preview from metadata for formats like HEIC.
    try:
        result = subprocess.run(
            ["exiftool", "-b", "-PreviewImage", str(source)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout:
        if Image is not None:
            try:
                with Image.open(io.BytesIO(result.stdout)) as img:
                    if square:
                        if save_square_jpeg(img, target, size=size):
                            return True
                    else:
                        if save_preview_jpeg(img, target, max_edge=max_edge):
                            return True
            except Exception:  # noqa: BLE001
                pass

    # macOS fallback: convert with sips for HEIC/HEIF and other unsupported inputs.
    if sys.platform == "darwin":
        temp_target = target.with_suffix(".tmp.jpg")
        command = ["sips", "-s", "format", "jpeg", "-Z", str(max(max_edge, size)), str(source), "--out", str(temp_target)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            result = None
        if result is not None and result.returncode == 0 and temp_target.exists():
            if square and Image is not None:
                try:
                    with Image.open(temp_target) as img:
                        if save_square_jpeg(img, target, size=size):
                            temp_target.unlink(missing_ok=True)
                            return True
                except Exception:  # noqa: BLE001
                    pass
            temp_target.replace(target)
            return True

    return False


def create_video_thumbnail(source: Path, target: Path, size: int = 320) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
            "-q:v",
            "4",
            str(target),
        ],
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={size}:-1",
            str(target),
        ],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return False
        if result.returncode == 0 and target.exists():
            return True
    return False


def ensure_thumbnail(record: MediaRecord) -> Path | None:
    key = cache_key(record, "thumb")
    target = THUMB_DIR / f"{key}.jpg"
    failed_marker = THUMB_DIR / f"{key}.failed"
    if target.exists():
        return target
    if failed_marker.exists():
        return None

    source = Path(record.path)
    if not source.exists():
        return None

    if record.media_type == "image":
        if create_image_derivative(source, target, square=True, size=320, max_edge=720):
            failed_marker.unlink(missing_ok=True)
            return target
        failed_marker.write_text("failed", encoding="utf-8")
        return None

    if record.media_type == "video":
        if create_video_thumbnail(source, target, size=320):
            failed_marker.unlink(missing_ok=True)
            return target
        failed_marker.write_text("failed", encoding="utf-8")
        return None

    return None


def ensure_preview(record: MediaRecord) -> Path | None:
    source = Path(record.path)
    if not source.exists():
        return None
    if record.media_type != "image":
        return source
    if record.extension in BROWSER_IMAGE_EXTENSIONS:
        return source

    key = cache_key(record, "preview")
    target = PREVIEW_DIR / f"{key}.jpg"
    failed_marker = PREVIEW_DIR / f"{key}.failed"
    if target.exists():
        return target
    if failed_marker.exists():
        return None
    if create_image_derivative(source, target, square=False, size=320, max_edge=2048):
        failed_marker.unlink(missing_ok=True)
        return target
    failed_marker.write_text("failed", encoding="utf-8")
    return None


def scan_worker(job: ScanJob) -> None:
    root = Path(job.root_path).expanduser().resolve()
    try:
        files = list_media_files(root)
        STATE.update_job_progress(job, total=len(files), processed=0)

        records: list[MediaRecord] = []
        located_count = 0
        unlocated_count = 0
        for idx, file_path in enumerate(files, start=1):
            try:
                record = STATE.get_or_build_record(file_path)
                records.append(record)
                if record.lat is not None and record.lon is not None:
                    located_count += 1
                else:
                    unlocated_count += 1
            except Exception:  # noqa: BLE001
                pass

            if idx % 8 == 0 or idx == len(files):
                STATE.update_job_progress(
                    job,
                    processed=idx,
                    located=located_count,
                    unlocated=unlocated_count,
                )

        STATE.complete_job(job, records)
        STATE.save_scan_cache(job.root_path, records)
        STATE.flush_meta_cache()
    except Exception as exc:  # noqa: BLE001
        STATE.fail_job(job, str(exc))


def pick_directory_with_dialog() -> str | None:
    if sys.platform == "darwin":
        # Use AppleScript instead of tkinter to avoid macOS AppKit main-thread crashes.
        script_lines = [
            'try',
            'POSIX path of (choose folder with prompt "选择需要扫描的媒体目录")',
            'on error number -128',
            'return ""',
            'end try',
        ]
        command = ["osascript"]
        for line in script_lines:
            command.extend(["-e", line])
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("系统缺少 osascript，无法打开目录选择器") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise RuntimeError(f"打开系统目录选择器失败: {detail or 'osascript exit != 0'}")
        selected = result.stdout.strip()
        if not selected:
            return None
        return str(Path(selected).expanduser().resolve())

    if sys.platform.startswith("linux"):
        for command in (
            ["zenity", "--file-selection", "--directory", "--title=选择需要扫描的媒体目录"],
            ["kdialog", "--getexistingdirectory", str(Path.home())],
        ):
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=False)
            except FileNotFoundError:
                continue
            if result.returncode == 0:
                selected = result.stdout.strip()
                if selected:
                    return str(Path(selected).expanduser().resolve())
                return None
            if result.returncode == 1:
                return None
        raise RuntimeError("未找到可用目录选择器（可安装 zenity 或 kdialog）")

    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description='选择需要扫描的媒体目录'; "
                "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}"
            ),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("系统缺少 powershell，无法打开目录选择器") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise RuntimeError(f"打开系统目录选择器失败: {detail or 'powershell exit != 0'}")
        selected = result.stdout.strip()
        if not selected:
            return None
        return str(Path(selected).expanduser().resolve())

    raise RuntimeError("当前系统暂不支持目录选择器，请手动输入路径")


class MediaMapHandler(BaseHTTPRequestHandler):
    server_version = "MediaMapBrowser/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.client_address[0]} - {fmt % args}")

    @staticmethod
    def _is_client_disconnect(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return True
        if isinstance(exc, OSError):
            return exc.errno in {32, 54, 104}
        return False

    def _safe_end_headers(self) -> bool:
        try:
            self.end_headers()
            return True
        except OSError as exc:
            if self._is_client_disconnect(exc):
                return False
            raise

    def _safe_write(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            return True
        except OSError as exc:
            if self._is_client_disconnect(exc):
                return False
            raise

    def _json_response(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if not self._safe_end_headers():
            return
        self._safe_write(body)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        try:
            content_length = int(length)
        except ValueError:
            return None
        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _serve_static(self, file_path: Path) -> None:
        try:
            safe_path = file_path.resolve()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            safe_path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not safe_path.exists() or not safe_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
        data = safe_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if not self._safe_end_headers():
            return
        self._safe_write(data)

    def _send_file(
        self,
        file_path: Path,
        content_type: str | None = None,
        *,
        cache_control: str | None = None,
    ) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        size = file_path.stat().st_size
        content_type = content_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        file_range = parse_range_header(self.headers.get("Range"), size)
        if file_range is None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Length", str(size))
            if not self._safe_end_headers():
                return
            with file_path.open("rb") as src:
                while True:
                    chunk = src.read(1024 * 256)
                    if not chunk:
                        break
                    if not self._safe_write(chunk):
                        return
            return

        start, end = file_range
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        if not self._safe_end_headers():
            return

        with file_path.open("rb") as src:
            src.seek(start)
            remaining = length
            while remaining > 0:
                chunk = src.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                if not self._safe_write(chunk):
                    return
                remaining -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            self._serve_static(STATIC_DIR / "index.html")
            return

        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            self._serve_static(STATIC_DIR / relative)
            return

        if parsed.path == "/api/boundaries/world":
            try:
                payload = read_cached_or_download(
                    WORLD_BOUNDARY_URL,
                    BOUNDARY_CACHE_DIR / "world_countries.geojson",
                )
            except RuntimeError as exc:
                self._json_response({"error": str(exc)}, status=502)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/geo+json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(payload)))
            if not self._safe_end_headers():
                return
            self._safe_write(payload)
            return

        if parsed.path == "/api/boundaries/china-provinces":
            try:
                payload = read_cached_or_download(
                    CHINA_PROVINCE_BOUNDARY_URL,
                    BOUNDARY_CACHE_DIR / "china_provinces.geojson",
                )
            except RuntimeError as exc:
                self._json_response({"error": str(exc)}, status=502)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/geo+json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(payload)))
            if not self._safe_end_headers():
                return
            self._safe_write(payload)
            return

        if parsed.path == "/api/boundaries/china-prefecture-cities":
            cache_path = BOUNDARY_CACHE_DIR / "china_prefecture_cities.geojson"
            payload: bytes
            if cache_path.exists():
                payload = cache_path.read_bytes()
                if not china_prefecture_cache_is_complete(payload):
                    try:
                        payload = build_china_prefecture_geojson()
                    except RuntimeError as exc:
                        self._json_response({"error": str(exc)}, status=502)
                        return
            else:
                try:
                    payload = build_china_prefecture_geojson()
                except RuntimeError as exc:
                    self._json_response({"error": str(exc)}, status=502)
                    return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/geo+json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(payload)))
            if not self._safe_end_headers():
                return
            self._safe_write(payload)
            return

        if parsed.path == "/api/scan/status":
            job_id = params.get("job_id", [""])[0]
            if not job_id:
                self._json_response({"error": "job_id is required"}, status=400)
                return
            job = STATE.get_job(job_id)
            if job is None:
                self._json_response({"error": "job not found"}, status=404)
                return
            self._json_response({"status": job.to_status()})
            return

        if parsed.path == "/api/cache/scans":
            self._json_response(
                {
                    "entries": STATE.list_scan_caches(),
                    "stats": STATE.cache_stats(),
                }
            )
            return

        if parsed.path == "/api/scan/result":
            job_id = params.get("job_id", [""])[0]
            if not job_id:
                self._json_response({"error": "job_id is required"}, status=400)
                return
            job = STATE.get_job(job_id)
            if job is None:
                self._json_response({"error": "job not found"}, status=404)
                return
            if job.status != "completed":
                self._json_response({"error": "job not completed"}, status=409)
                return

            located = [r.to_item() for r in job.records if r.lat is not None and r.lon is not None]
            unlocated = [r.to_item() for r in job.records if r.lat is None or r.lon is None]
            self._json_response(
                {
                    "summary": {
                        "total": len(job.records),
                        "located": len(located),
                        "unlocated": len(unlocated),
                    },
                    "items": located,
                    "unlocated": unlocated,
                }
            )
            return

        if parsed.path == "/api/thumbnail":
            media_id = params.get("id", [""])[0]
            record = STATE.get_media(media_id)
            if record is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            thumbnail = ensure_thumbnail(record)
            if thumbnail is None:
                placeholder = placeholder_for(record)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Cache-Control", "public, max-age=120")
                self.send_header("Content-Length", str(len(placeholder)))
                if not self._safe_end_headers():
                    return
                self._safe_write(placeholder)
                return

            self._send_file(
                thumbnail,
                content_type="image/jpeg",
                cache_control="public, max-age=31536000, immutable",
            )
            return

        if parsed.path == "/api/preview":
            media_id = params.get("id", [""])[0]
            record = STATE.get_media(media_id)
            if record is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            preview = ensure_preview(record)
            if preview is None:
                thumbnail = ensure_thumbnail(record)
                if thumbnail is not None:
                    self._send_file(
                        thumbnail,
                        content_type="image/jpeg",
                        cache_control="public, max-age=31536000, immutable",
                    )
                    return
                placeholder = placeholder_for(record)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Cache-Control", "public, max-age=120")
                self.send_header("Content-Length", str(len(placeholder)))
                if not self._safe_end_headers():
                    return
                self._safe_write(placeholder)
                return

            preview_type = "image/jpeg" if preview.suffix.lower() == ".jpg" else None
            self._send_file(
                preview,
                content_type=preview_type,
                cache_control="public, max-age=31536000, immutable",
            )
            return

        if parsed.path == "/api/file":
            media_id = params.get("id", [""])[0]
            record = STATE.get_media(media_id)
            if record is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(Path(record.path))
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/pick-directory":
            try:
                selected = pick_directory_with_dialog()
            except RuntimeError as exc:
                self._json_response({"error": str(exc)}, status=500)
                return
            self._json_response({"path": selected})
            return

        if parsed.path == "/api/cache/clear":
            stats = STATE.clear_all_cache()
            self._json_response({"ok": True, "stats": stats})
            return

        if parsed.path == "/api/cache/load":
            payload = self._read_json_body()
            if payload is None:
                self._json_response({"error": "invalid json"}, status=400)
                return
            scan_ids_raw = payload.get("scan_ids")
            if isinstance(scan_ids_raw, list):
                scan_ids = [str(x).strip() for x in scan_ids_raw if str(x).strip()]
                if not scan_ids:
                    self._json_response({"error": "scan_ids is required"}, status=400)
                    return
                merged_payload, loaded_scan_ids, _ = STATE.load_scan_caches(scan_ids)
                if not loaded_scan_ids:
                    self._json_response({"error": "scan cache not found"}, status=404)
                    return
                self._json_response(merged_payload)
                return
            scan_id = str(payload.get("scan_id", "")).strip()
            if not scan_id:
                self._json_response({"error": "scan_id is required"}, status=400)
                return
            cached = STATE.load_scan_cache(scan_id)
            if cached is None:
                self._json_response({"error": "scan cache not found"}, status=404)
                return
            cached["loaded_scan_ids"] = [scan_id]
            cached["missing_scan_ids"] = []
            self._json_response(cached)
            return

        if parsed.path == "/api/cache/delete":
            payload = self._read_json_body()
            if payload is None:
                self._json_response({"error": "invalid json"}, status=400)
                return
            scan_id = str(payload.get("scan_id", "")).strip()
            if not scan_id:
                self._json_response({"error": "scan_id is required"}, status=400)
                return
            removed = STATE.delete_scan_cache(scan_id)
            self._json_response({"ok": removed, "stats": STATE.cache_stats()})
            return

        if parsed.path != "/api/scan":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json_body()
        if payload is None:
            self._json_response({"error": "invalid json"}, status=400)
            return

        root_path_raw = str(payload.get("path", "")).strip()
        if not root_path_raw:
            self._json_response({"error": "path is required"}, status=400)
            return

        root_path = Path(root_path_raw).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            self._json_response({"error": "path not found or not a directory"}, status=400)
            return

        job = STATE.start_job(str(root_path))
        thread = threading.Thread(target=scan_worker, args=(job,), daemon=True)
        thread.start()

        self._json_response(
            {
                "job_id": job.job_id,
                "status": job.status,
                "scan_id": sha1_text(str(root_path)),
            }
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local media map browser server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), MediaMapHandler)
    print(f"Media Map Browser running at http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.flush_meta_cache()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
