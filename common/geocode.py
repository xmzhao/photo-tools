"""Shared reverse-geocoding helpers."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
TIANDITU_REGEO_URL = "https://api.tianditu.gov.cn/geocoder"


def _request_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected reverse geocode response format")
    return data


def _normalize_city(value: Any, fallback_province: Any) -> str:
    if isinstance(value, list):
        city = str(value[0]) if value else ""
    else:
        city = str(value or "")
    city = city.strip()
    if city:
        return city
    return str(fallback_province or "").strip()


def reverse_geocode_amap(latitude: float, longitude: float, amap_key: str) -> dict[str, Any]:
    query = {
        "key": amap_key,
        "location": f"{longitude:.8f},{latitude:.8f}",
        "extensions": "all",
        "radius": "500",
        "output": "json",
    }
    url = f"{AMAP_REGEO_URL}?{urlencode(query)}"
    data = _request_json(url)

    if data.get("status") != "1":
        info = data.get("info", "unknown")
        raise RuntimeError(f"Amap reverse geocode failed: {info}")

    regeo = data.get("regeocode") or {}
    address_component = regeo.get("addressComponent") or {}
    city = _normalize_city(address_component.get("city"), address_component.get("province"))
    pois = regeo.get("pois") or []
    if not isinstance(pois, list):
        pois = []

    return {
        "provider": "amap",
        "city": city,
        "formatted_address": str(regeo.get("formatted_address", "")).strip(),
        "pois": pois,
    }


def reverse_geocode_tianditu(
    latitude: float,
    longitude: float,
    tianditu_key: str,
) -> dict[str, Any]:
    post_str = json.dumps({"lon": longitude, "lat": latitude, "ver": 1}, ensure_ascii=False)
    query = {
        "postStr": post_str,
        "type": "geocode",
        "tk": tianditu_key,
    }
    url = f"{TIANDITU_REGEO_URL}?{urlencode(query)}"
    data = _request_json(url)

    status = str(data.get("status", ""))
    if status not in {"0", "200"}:
        msg = data.get("msg", "unknown")
        raise RuntimeError(f"Tianditu reverse geocode failed: {msg}")

    result = data.get("result") or {}
    address_component = result.get("addressComponent") or {}
    city = _normalize_city(address_component.get("city"), address_component.get("province"))
    pois = result.get("pois") or []
    if not isinstance(pois, list):
        pois = []

    return {
        "provider": "tianditu",
        "city": city,
        "formatted_address": str(result.get("formatted_address", "")).strip(),
        "pois": pois,
    }

