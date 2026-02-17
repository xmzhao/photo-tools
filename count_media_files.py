#!/usr/bin/env python3
"""Count media files and vote top city POI per directory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from common.geocode import reverse_geocode_amap, reverse_geocode_tianditu
from common.gps import extract_gps
from common.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


class MediaCounts(NamedTuple):
    image_total: int
    video_total: int

    @property
    def media_total(self) -> int:
        return self.image_total + self.video_total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count image/video files and print city-POI top votes per directory."
    )
    parser.add_argument(
        "directory",
        help="Root directory to scan recursively.",
    )
    parser.add_argument(
        "--provider",
        choices=("amap", "tianditu"),
        default="amap",
        help="Reverse geocoding provider (default: amap).",
    )
    parser.add_argument(
        "--amap-key",
        default=os.getenv("AMAP_KEY", ""),
        help="Amap key, or read from AMAP_KEY.",
    )
    parser.add_argument(
        "--tianditu-key",
        default=os.getenv("TIANDITU_KEY", ""),
        help="Tianditu key, or read from TIANDITU_KEY.",
    )
    parser.add_argument(
        "--sort-by",
        choices=("path", "media_total"),
        default="path",
        help="Sort output rows by path or media_total (default: path).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of plain text.",
    )
    return parser.parse_args()


def to_city_poi(city: str, poi: str) -> str | None:
    if city and poi:
        return f"{city}-{poi}"
    if city:
        return city
    if poi:
        return poi
    return None


def reverse_geocode_city_poi(
    latitude: float,
    longitude: float,
    provider: str,
    amap_key: str,
    tianditu_key: str,
) -> str | None:
    try:
        if provider == "amap":
            if not amap_key:
                return None
            geo = reverse_geocode_amap(latitude, longitude, amap_key)
        else:
            if not tianditu_key:
                return None
            geo = reverse_geocode_tianditu(latitude, longitude, tianditu_key)
    except Exception:  # noqa: BLE001
        return None

    top_poi = geo["pois"][0] if geo["pois"] else {}
    return to_city_poi(geo["city"], str(top_poi.get("name", "")).strip())


def scan_direct_media_counts(root: Path) -> dict[Path, MediaCounts]:
    direct_counts: dict[Path, MediaCounts] = {}

    for current_root, _, files in os.walk(root):
        current_path = Path(current_root)
        image_count = 0
        video_count = 0

        for filename in files:
            extension = Path(filename).suffix.lower()
            if extension in IMAGE_EXTENSIONS:
                image_count += 1
            elif extension in VIDEO_EXTENSIONS:
                video_count += 1

        direct_counts[current_path] = MediaCounts(image_count, video_count)

    return direct_counts


def scan_direct_poi_votes(
    root: Path,
    provider: str,
    amap_key: str,
    tianditu_key: str,
) -> dict[Path, Counter[str]]:
    direct_votes: dict[Path, Counter[str]] = {}
    geo_cache: dict[tuple[str, float, float], str | None] = {}

    for current_root, _, files in os.walk(root):
        current_path = Path(current_root)
        votes: Counter[str] = Counter()

        for filename in files:
            file_path = current_path / filename
            extension = file_path.suffix.lower()
            if extension not in IMAGE_EXTENSIONS and extension not in VIDEO_EXTENSIONS:
                continue

            gps = extract_gps(file_path, image_extensions=IMAGE_EXTENSIONS)
            if gps is None:
                continue

            latitude, longitude = gps
            cache_key = (provider, round(latitude, 6), round(longitude, 6))
            if cache_key in geo_cache:
                city_poi = geo_cache[cache_key]
            else:
                city_poi = reverse_geocode_city_poi(
                    latitude=latitude,
                    longitude=longitude,
                    provider=provider,
                    amap_key=amap_key,
                    tianditu_key=tianditu_key,
                )
                geo_cache[cache_key] = city_poi

            if city_poi:
                votes[city_poi] += 1

        direct_votes[current_path] = votes

    return direct_votes


def aggregate_media_counts(
    root: Path,
    direct_counts: dict[Path, MediaCounts],
) -> dict[Path, MediaCounts]:
    totals: dict[Path, list[int]] = {
        path: [counts.image_total, counts.video_total]
        for path, counts in direct_counts.items()
    }

    for directory in sorted(direct_counts.keys(), key=lambda p: len(p.parts), reverse=True):
        if directory == root:
            continue
        parent = directory.parent
        if parent not in totals:
            continue
        totals[parent][0] += totals[directory][0]
        totals[parent][1] += totals[directory][1]

    return {
        path: MediaCounts(values[0], values[1])
        for path, values in totals.items()
    }


def aggregate_votes(
    root: Path,
    direct_votes: dict[Path, Counter[str]],
) -> dict[Path, Counter[str]]:
    totals = {path: Counter(votes) for path, votes in direct_votes.items()}

    for directory in sorted(direct_votes.keys(), key=lambda p: len(p.parts), reverse=True):
        if directory == root:
            continue
        parent = directory.parent
        if parent not in totals:
            continue
        totals[parent].update(totals[directory])

    return totals


def build_children_map(root: Path, paths: list[Path]) -> dict[Path, list[Path]]:
    children: dict[Path, list[Path]] = {path: [] for path in paths}
    for path in paths:
        if path == root:
            continue
        parent = path.parent
        if parent in children:
            children[parent].append(path)
    return children


def verify_parent_rollup(
    direct_counts: dict[Path, MediaCounts],
    total_counts: dict[Path, MediaCounts],
    children: dict[Path, list[Path]],
) -> bool:
    for path in direct_counts:
        expected_image = direct_counts[path].image_total + sum(
            total_counts[child].image_total for child in children[path]
        )
        expected_video = direct_counts[path].video_total + sum(
            total_counts[child].video_total for child in children[path]
        )
        actual = total_counts[path]
        if expected_image != actual.image_total or expected_video != actual.video_total:
            return False
    return True


def verify_vote_rollup(
    direct_votes: dict[Path, Counter[str]],
    total_votes: dict[Path, Counter[str]],
    children: dict[Path, list[Path]],
) -> bool:
    for path, own_votes in direct_votes.items():
        expected = Counter(own_votes)
        for child in children[path]:
            expected.update(total_votes[child])
        if expected != total_votes[path]:
            return False
    return True


def top_two(votes: Counter[str]) -> list[tuple[str, int]]:
    ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))[:2]
    while len(ranked) < 2:
        ranked.append(("N/A", 0))
    return ranked


def sort_directory_rows(
    rows: list[tuple[Path, MediaCounts]],
    sort_by: str,
) -> list[tuple[Path, MediaCounts]]:
    if sort_by == "media_total":
        return sorted(rows, key=lambda item: (-item[1].media_total, str(item[0])))
    return sorted(rows, key=lambda item: str(item[0]))


def main() -> int:
    args = parse_args()
    root = Path(args.directory).expanduser().resolve()

    if not root.exists():
        print(f"Error: directory does not exist: {root}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        return 1

    if args.provider == "amap" and not args.amap_key:
        print("Error: provider=amap requires --amap-key or AMAP_KEY.", file=sys.stderr)
        return 1
    if args.provider == "tianditu" and not args.tianditu_key:
        print(
            "Error: provider=tianditu requires --tianditu-key or TIANDITU_KEY.",
            file=sys.stderr,
        )
        return 1

    direct_counts = scan_direct_media_counts(root)
    direct_votes = scan_direct_poi_votes(
        root=root,
        provider=args.provider,
        amap_key=args.amap_key,
        tianditu_key=args.tianditu_key,
    )

    total_counts = aggregate_media_counts(root, direct_counts)
    total_votes = aggregate_votes(root, direct_votes)

    paths = list(direct_counts.keys())
    children = build_children_map(root, paths)
    counts_verified = verify_parent_rollup(direct_counts, total_counts, children)
    votes_verified = verify_vote_rollup(direct_votes, total_votes, children)

    if not counts_verified or not votes_verified:
        print("Error: parent directory rollup verification failed.", file=sys.stderr)
        return 1

    sorted_rows = sort_directory_rows(list(total_counts.items()), args.sort_by)

    if args.json:
        rows = []
        for directory, counts in sorted_rows:
            top = top_two(total_votes[directory])
            rows.append(
                {
                    "directory": str(directory),
                    "media_total": counts.media_total,
                    "image_total": counts.image_total,
                    "video_total": counts.video_total,
                    "poi_top1": {
                        "city_poi": top[0][0],
                        "support_file_count": top[0][1],
                    },
                    "poi_top2": {
                        "city_poi": top[1][0],
                        "support_file_count": top[1][1],
                    },
                }
            )

        root_counts = total_counts[root]
        root_top = top_two(total_votes[root])
        payload = {
            "directory": str(root),
            "media_total": root_counts.media_total,
            "image_total": root_counts.image_total,
            "video_total": root_counts.video_total,
            "poi_top1": {
                "city_poi": root_top[0][0],
                "support_file_count": root_top[0][1],
            },
            "poi_top2": {
                "city_poi": root_top[1][0],
                "support_file_count": root_top[1][1],
            },
            "sort_by": args.sort_by,
            "parent_rollup_verified": True,
            "per_directory": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for directory, counts in sorted_rows:
        top = top_two(total_votes[directory])
        print(
            f"{directory}\t{counts.media_total}, {counts.image_total}, {counts.video_total}"
            f"\t{top[0][0]},{top[0][1]}"
            f"\t{top[1][0]},{top[1][1]}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
