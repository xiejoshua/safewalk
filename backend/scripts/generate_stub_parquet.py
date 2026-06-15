#!/usr/bin/env python3
"""
Generate a stub scored_segments.parquet for local development.

R3/R4 will replace this with real prebaked data from prebake.py.
BBox is read from corridor.json at the repo root (Gillem corridor).
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORRIDOR_PATH = REPO_ROOT / "corridor.json"
GILLEM_BBOX = (-84.37, 33.58, -84.29, 33.65)
SEGMENT_LENGTH_DEG = 0.001  # ~100 m at this latitude — sparse stub grid


def load_bbox() -> tuple[float, float, float, float]:
    if CORRIDOR_PATH.exists():
        with CORRIDOR_PATH.open(encoding="utf-8") as f:
            corridor = json.load(f)
        bbox = corridor["bbox"]
        return tuple(float(v) for v in bbox)  # type: ignore[return-value]
    return GILLEM_BBOX


def _grid_segments(bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    min_lon, min_lat, max_lon, max_lat = bbox
    segments: list[LineString] = []
    ids: list[str] = []

    lat = min_lat
    row = 0
    while lat < max_lat:
        lon = min_lon
        col = 0
        while lon < max_lon:
            end_lon = min(lon + SEGMENT_LENGTH_DEG, max_lon)
            end_lat = min(lat + SEGMENT_LENGTH_DEG * 0.4, max_lat)
            segments.append(LineString([(lon, lat), (end_lon, end_lat)]))
            ids.append(f"stub-{row:04d}-{col:04d}")
            lon += SEGMENT_LENGTH_DEG
            col += 1
        lat += SEGMENT_LENGTH_DEG * 0.4
        row += 1

    n = len(segments)
    rng = np.random.default_rng(42)
    is_crossing = rng.choice([False, True], n, p=[0.9, 0.1])
    crossing_penalty = np.where(
        is_crossing,
        rng.uniform(0.05, 0.3, n),
        0.0,
    )

    gdf = gpd.GeoDataFrame(
        {
            "segment_id": ids,
            "sidewalk_cov": rng.uniform(0.0, 1.0, n),
            "traffic_risk": rng.uniform(0.0, 0.8, n),
            "crash_norm": rng.uniform(0.0, 0.6, n),
            "hazard_norm": rng.uniform(0.0, 0.3, n),
            "canopy_pct": rng.uniform(0.0, 0.7, n),
            "exposure_norm": rng.uniform(0.2, 0.8, n),
            "slope_risk": np.clip(rng.uniform(0.0, 0.4, n), 0.0, 1.0),
            "barrier": rng.choice([False, True], n, p=[0.95, 0.05]),
            "crossing_penalty": crossing_penalty.astype(float),
            "lanes": rng.choice([2.0, 4.0, 6.0], n),
            "highway": rng.choice(["residential", "tertiary", "footway"], n),
            "wheelchair": rng.choice([None, "yes", "no"], n, p=[0.9, 0.08, 0.02]),
        },
        geometry=segments,
        crs="EPSG:4326",
    )
    return gdf.set_index("segment_id", drop=False)


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "data" / "scored_segments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    bbox = load_bbox()
    gdf = _grid_segments(bbox)
    gdf.to_parquet(out)
    print(f"Wrote {len(gdf)} stub segments to {out} (bbox={bbox})")


if __name__ == "__main__":
    main()
