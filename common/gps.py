"""Shared GPS extraction helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.process import run_json_command

try:
    from PIL import Image
    from PIL.ExifTags import GPSTAGS, TAGS
except ImportError:  # pragma: no cover - optional dependency.
    Image = None
    GPSTAGS = {}
    TAGS = {}


def parse_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def ratio_to_float(value: Any) -> float:
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return float(value.numerator) / float(value.denominator)
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[0]) / float(value[1])
    return float(value)


def dms_to_decimal(dms: Any, ref: str) -> float:
    degrees = ratio_to_float(dms[0])
    minutes = ratio_to_float(dms[1])
    seconds = ratio_to_float(dms[2])
    decimal = degrees + minutes / 60 + seconds / 3600
    if ref in {"S", "W"}:
        decimal = -decimal
    return decimal


def extract_gps_with_exiftool(file_path: Path) -> tuple[float, float] | None:
    payload = run_json_command(
        ["exiftool", "-j", "-n", "-GPSLatitude", "-GPSLongitude", str(file_path)]
    )
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None

    record = payload[0]
    latitude = parse_number(record.get("GPSLatitude"))
    longitude = parse_number(record.get("GPSLongitude"))
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def extract_gps_with_pillow(file_path: Path) -> tuple[float, float] | None:
    if Image is None:
        return None

    try:
        with Image.open(file_path) as img:
            exif_raw = img._getexif() or {}
    except Exception:  # noqa: BLE001
        return None

    exif = {TAGS.get(tag, tag): value for tag, value in exif_raw.items()}
    gps_info_raw = exif.get("GPSInfo")
    if not gps_info_raw:
        return None

    gps_info = {GPSTAGS.get(tag, tag): value for tag, value in gps_info_raw.items()}
    lat = gps_info.get("GPSLatitude")
    lat_ref = gps_info.get("GPSLatitudeRef")
    lon = gps_info.get("GPSLongitude")
    lon_ref = gps_info.get("GPSLongitudeRef")
    if not all([lat, lat_ref, lon, lon_ref]):
        return None

    try:
        latitude = dms_to_decimal(lat, str(lat_ref))
        longitude = dms_to_decimal(lon, str(lon_ref))
    except Exception:  # noqa: BLE001
        return None

    return latitude, longitude


def extract_gps(file_path: Path, *, image_extensions: set[str]) -> tuple[float, float] | None:
    gps = extract_gps_with_exiftool(file_path)
    if gps is not None:
        return gps

    if file_path.suffix.lower() in image_extensions:
        return extract_gps_with_pillow(file_path)
    return None

