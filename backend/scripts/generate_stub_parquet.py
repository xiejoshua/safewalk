#!/usr/bin/env python3
"""
Generate a stub scored_segments.parquet for local development.

R3/R4 will replace this with real prebaked data from prebake.py.
Covers the Gillem Logistics demo corridor (approximate bbox).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

# Gillem corridor approximate bounds (lon/lat)
BBOX = (-84.42, 33.68, -84.33, 33.72)
SEGMENT_LENGTH_DEG = 0.001  # ~100 m at this latitude — sparse stub grid


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
            ids.append(f"seg_{row}_{col}")
            lon += SEGMENT_LENGTH_DEG
            col += 1
        lat += SEGMENT_LENGTH_DEG * 0.4
        row += 1

    n = len(segments)
    rng = np.random.default_rng(42)

    return gpd.GeoDataFrame(
        {
            "segment_id": ids,
            "sidewalk_cov": rng.uniform(0.0, 1.0, n),
            "traffic_risk": rng.uniform(0.0, 0.8, n),
            "crash_norm": rng.uniform(0.0, 0.6, n),
            "hazard_norm": rng.uniform(0.0, 0.3, n),
            "canopy_pct": rng.uniform(0.0, 0.7, n),
            "exposure_norm": rng.uniform(0.2, 0.8, n),
            "slope_risk": rng.uniform(0.0, 0.4, n),
            "barrier": rng.choice([False, True], n, p=[0.95, 0.05]),
            "is_crossing": rng.choice([False, True], n, p=[0.9, 0.1]),
            "lanes": rng.choice([2, 4, 6], n),
            "traffic_signals": rng.choice([None, "signal"], n),
            "crossing": rng.choice([None, "unmarked", "traffic_signals"], n),
            "highway": rng.choice(["residential", "tertiary", "footway"], n),
            "wheelchair": rng.choice([None, "yes", "no"], n, p=[0.9, 0.08, 0.02]),
        },
        geometry=segments,
        crs="EPSG:4326",
    )


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "data" / "scored_segments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    gdf = _grid_segments(BBOX)
    gdf.to_parquet(out)
    print(f"Wrote {len(gdf)} stub segments to {out}")


if __name__ == "__main__":
    main()
