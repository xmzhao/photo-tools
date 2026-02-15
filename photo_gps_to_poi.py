#!/usr/bin/env python3
"""读取照片拍摄经纬度并转换为中文 POI 地址。

用法示例：
  python3 scripts/photo_gps_to_poi.py /path/to/photo.jpg --provider amap --amap-key <你的高德Key>
  python3 scripts/photo_gps_to_poi.py /path/to/photo.jpg --provider tianditu --tianditu-key <你的天地图Key>
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS


AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
TIANDITU_REGEO_URL = "https://api.tianditu.gov.cn/geocoder"


def _ratio_to_float(value: Any) -> float:
    """兼容 Pillow 中 IFDRational / tuple 等格式。"""
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return float(value.numerator) / float(value.denominator)
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[0]) / float(value[1])
    return float(value)


def _dms_to_decimal(dms: Any, ref: str) -> float:
    degrees = _ratio_to_float(dms[0])
    minutes = _ratio_to_float(dms[1])
    seconds = _ratio_to_float(dms[2])
    decimal = degrees + minutes / 60 + seconds / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract_gps(photo_path: str) -> Tuple[float, float]:
    with Image.open(photo_path) as img:
        exif_raw = img._getexif() or {}

    exif: Dict[str, Any] = {TAGS.get(tag, tag): value for tag, value in exif_raw.items()}
    gps_info_raw = exif.get("GPSInfo")
    if not gps_info_raw:
        raise ValueError("照片中没有 GPS 信息")

    gps_info = {GPSTAGS.get(tag, tag): value for tag, value in gps_info_raw.items()}
    lat = gps_info.get("GPSLatitude")
    lat_ref = gps_info.get("GPSLatitudeRef")
    lon = gps_info.get("GPSLongitude")
    lon_ref = gps_info.get("GPSLongitudeRef")

    if not all([lat, lat_ref, lon, lon_ref]):
        raise ValueError("GPS 字段不完整，无法解析经纬度")

    latitude = _dms_to_decimal(lat, str(lat_ref))
    longitude = _dms_to_decimal(lon, str(lon_ref))
    return latitude, longitude


def reverse_geocode_amap(latitude: float, longitude: float, amap_key: str) -> Dict[str, Any]:
    query = {
        "key": amap_key,
        "location": f"{longitude:.8f},{latitude:.8f}",
        "extensions": "all",
        "radius": "500",
        "output": "json",
    }
    url = f"{AMAP_REGEO_URL}?{urlencode(query)}"

    with urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("status") != "1":
        info = data.get("info", "未知错误")
        raise RuntimeError(f"高德逆地理编码失败: {info}")

    regeo = data.get("regeocode", {})
    pois = regeo.get("pois") or []
    top_poi: Optional[Dict[str, Any]] = pois[0] if pois else None

    return {
        "formatted_address": regeo.get("formatted_address", ""),
        "poi_name": (top_poi or {}).get("name", ""),
        "poi_address": (top_poi or {}).get("address", ""),
        "distance_m": (top_poi or {}).get("distance", ""),
        "provider": "amap",
    }


def reverse_geocode_tianditu(latitude: float, longitude: float, tianditu_key: str) -> Dict[str, Any]:
    post_str = json.dumps({"lon": longitude, "lat": latitude, "ver": 1}, ensure_ascii=False)
    query = {
        "postStr": post_str,
        "type": "geocode",
        "tk": tianditu_key,
    }
    url = f"{TIANDITU_REGEO_URL}?{urlencode(query)}"

    with urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # 天地图 status 约定通常为 "0" 表示成功；这里兼容字符串/数字两种格式。
    status = str(data.get("status", ""))
    if status not in {"0", "200"}:
        msg = data.get("msg", "未知错误")
        raise RuntimeError(f"天地图逆地理编码失败: {msg}")

    result = data.get("result") or {}
    pois = result.get("pois") or []
    top_poi: Optional[Dict[str, Any]] = pois[0] if pois else None

    return {
        "formatted_address": result.get("formatted_address", ""),
        "poi_name": (top_poi or {}).get("name", ""),
        "poi_address": (top_poi or {}).get("addr", ""),
        "distance_m": (top_poi or {}).get("distance", ""),
        "provider": "tianditu",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="读取照片拍摄经纬度并转换为中文 POI 地址")
    parser.add_argument("photo", help="照片路径（jpg/heic 等，需包含 EXIF GPS）")
    parser.add_argument(
        "--provider",
        choices=("amap", "tianditu"),
        default="amap",
        help="逆地理编码服务商（默认: amap）",
    )
    parser.add_argument("--amap-key", help="高德开放平台 Web 服务 Key")
    parser.add_argument("--tianditu-key", help="天地图 Web 服务 Key")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args()

    try:
        lat, lon = extract_gps(args.photo)
        if args.provider == "amap":
            if not args.amap_key:
                raise ValueError("使用 amap 时必须提供 --amap-key")
            geo = reverse_geocode_amap(lat, lon, args.amap_key)
        else:
            if not args.tianditu_key:
                raise ValueError("使用 tianditu 时必须提供 --tianditu-key")
            geo = reverse_geocode_tianditu(lat, lon, args.tianditu_key)
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    result = {
        "latitude": lat,
        "longitude": lon,
        **geo,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"服务商: {result['provider']}")
        print(f"拍摄经纬度: {lat:.8f}, {lon:.8f}")
        print(f"中文地址: {result['formatted_address'] or '（无）'}")
        print(f"最近POI: {result['poi_name'] or '（无）'}")
        print(f"POI地址: {result['poi_address'] or '（无）'}")
        if result["distance_m"]:
            print(f"距离: {result['distance_m']} 米")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
