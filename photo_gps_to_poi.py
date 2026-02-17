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
from pathlib import Path
from typing import Any

from common.geocode import reverse_geocode_amap, reverse_geocode_tianditu
from common.gps import extract_gps
from common.media import IMAGE_EXTENSIONS


def build_poi_result(geo: dict[str, Any]) -> dict[str, Any]:
    top_poi = geo["pois"][0] if geo["pois"] else {}
    poi_address = top_poi.get("address", "")
    if not poi_address:
        poi_address = top_poi.get("addr", "")
    return {
        "formatted_address": geo["formatted_address"],
        "poi_name": top_poi.get("name", ""),
        "poi_address": poi_address,
        "distance_m": top_poi.get("distance", ""),
        "provider": geo["provider"],
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
        gps = extract_gps(Path(args.photo), image_extensions=IMAGE_EXTENSIONS)
        if gps is None:
            raise ValueError("照片中没有可用 GPS 信息")
        lat, lon = gps
        if args.provider == "amap":
            if not args.amap_key:
                raise ValueError("使用 amap 时必须提供 --amap-key")
            geo = build_poi_result(reverse_geocode_amap(lat, lon, args.amap_key))
        else:
            if not args.tianditu_key:
                raise ValueError("使用 tianditu 时必须提供 --tianditu-key")
            geo = build_poi_result(reverse_geocode_tianditu(lat, lon, args.tianditu_key))
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
