"""
OSM/Overpass I/O and raw-JSON parsing for Safewalk's walk network.

This module is intentionally GeoPandas-free so it can be imported by lightweight
scripts (e.g. ``scripts/validate_corridor.py``) without pulling the geo stack.
All geometry assembly lives in ``network.build``.

The walk-eligibility set and three-clause filter (``highway``/``foot``/``access``)
are the single source of truth used by both validation and the build pipeline.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / "data" / "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

WALK_ELIGIBLE: set[str] = {
    "footway", "path", "pedestrian", "steps", "living_street",
    "residential", "service", "unclassified",
    "tertiary", "tertiary_link",
    "secondary", "secondary_link",
    "primary", "primary_link",
}


def is_walk_eligible(tags: dict) -> bool:
    """Walk-eligibility filter applied to an OSM way's tag dict.

    Three clauses (per DESIGN.md §7d):
      1. ``highway`` is in :data:`WALK_ELIGIBLE`
      2. ``foot`` is not explicitly ``"no"``
      3. ``access`` is not ``"no"`` or ``"private"``
    """
    if not tags:
        return False
    if tags.get("highway") not in WALK_ELIGIBLE:
        return False
    if tags.get("foot") == "no":
        return False
    if tags.get("access") in {"no", "private"}:
        return False
    return True


def load_cached_osm(name: str) -> dict:
    """Read the cached Overpass JSON for a named corridor.

    Hard-fails if the cache is missing — re-querying Overpass is gated behind
    :func:`fetch_overpass` (per TASKS.md line 12: reuse the cache, don't re-query).
    """
    cache_path = CACHE_DIR / f"{name}.json"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"OSM cache not found: {cache_path}. "
            f"Run fetch_overpass() explicitly to populate it."
        )
    return json.loads(cache_path.read_text())


def fetch_overpass(bbox_wsen: list[float], cache_path: Path) -> dict:
    """Fetch the walk-network Overpass query for a bbox, caching the result.

    Kept as a fallback for future corridors. Not invoked on Gillem — the cache
    is already in place.

    Headers: ASCII-only ``User-Agent``; POSTs the raw query as the request body
    (form-encoding triggers HTTP 406, per TASKS.md line 12).
    """
    if cache_path.exists():
        print(f"  cache hit: {cache_path}", file=sys.stderr)
        return json.loads(cache_path.read_text())

    w, s, e, n = bbox_wsen
    query = f"""
[out:json][timeout:90][maxsize:1073741824];
(
  way["highway"]({s},{w},{n},{e});
  node["highway"="crossing"]({s},{w},{n},{e});
  node["highway"="traffic_signals"]({s},{w},{n},{e});
  node["highway"="bus_stop"]({s},{w},{n},{e});
  node["public_transport"="platform"]({s},{w},{n},{e});
);
out body;
>;
out skel qt;
""".strip()

    headers = {
        "User-Agent": "safewalk-hackathon/0.1 (joshua.xie@coxautoinc.com)",
        "Accept": "application/json",
    }
    print(f"  fetching {OVERPASS_URL} for bbox ({s},{w},{n},{e}) ...", file=sys.stderr)
    t0 = time.time()
    r = requests.post(OVERPASS_URL, data=query.encode("utf-8"), headers=headers, timeout=120)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:400]}", file=sys.stderr)
    r.raise_for_status()
    data = r.json()
    print(
        f"  fetched in {time.time() - t0:.1f}s, {len(data.get('elements', []))} elements",
        file=sys.stderr,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data))
    return data


def parse_elements(data: dict) -> tuple[dict[int, tuple[float, float]], list[dict]]:
    """Split an Overpass response into ``(nodes_by_id, ways)``.

    Node tuples are ``(lon, lat)`` — matches Shapely's ``Point(x, y)`` convention.
    Way dicts are passed through as-is; consumers should read ``el["tags"]`` and
    ``el["nodes"]``.

    Note: node tags are intentionally discarded here for the network-build path
    (only geometry needed). Modules that need node tags (e.g. ``layers/crossing.py``
    reading ``highway=crossing`` nodes) should call :func:`parse_node_tags`.
    """
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict] = []
    for el in data.get("elements", []):
        et = el.get("type")
        if et == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])
        elif et == "way":
            ways.append(el)
    return nodes, ways


def parse_node_tags(data: dict) -> dict[int, dict]:
    """Return ``{node_id: tags_dict}`` for nodes that carry any tags.

    Nodes without tags are omitted (most nodes in OSM are just geometry vertices
    with no semantic content). This keeps the returned dict small and avoids
    duplicating :func:`parse_elements`' coordinate-only return.

    Used by ``layers/crossing.py`` to find ``highway=crossing`` and
    ``highway=traffic_signals`` nodes plus their full tag attributes
    (``crossing``, ``crossing:signals``, ``crossing:markings``, ``kerb``,
    ``wheelchair``, ``tactile_paving``, …).
    """
    out: dict[int, dict] = {}
    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        tags = el.get("tags")
        if tags:
            out[el["id"]] = tags
    return out
