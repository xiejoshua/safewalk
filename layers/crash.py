"""crash.py — crash_norm factor module.

Data source: backend/data/Crashes_2020-2024.geojson (statewide GDOT crash data)
Filter: pedestrian crashes only (F__of_Pedestrians_per_crash > 0)
        in Clayton County only (Gillem Corridor demo scope)
Weight: KABCO severity (fatal > serious > minor > PDO)
Method: 30 m buffer per segment, weighted crash count, min-max normalize

Null policy: segment with no pedestrian crashes within 30 m → crash_norm = 0.0
             (not known to be dangerous; absence of data ≠ safe)
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from layers._utils import normalize, weight_by_kabco

_DATA_FILE = Path(__file__).resolve().parent.parent / "backend" / "data" / "Crashes_2020-2024.geojson"
_BUFFER_M = 30.0
_TARGET_COUNTIES = {"Clayton"}


def _load_crashes() -> gpd.GeoDataFrame:
    crashes = gpd.read_file(_DATA_FILE)

    # Filter to pedestrian-involved crashes
    ped_mask = crashes["F__of_Pedestrians_per_crash"].fillna(0) > 0
    crashes = crashes[ped_mask].copy()

    # Filter to Clayton County (Gillem Corridor demo)
    county_mask = crashes["Area__County"].isin(_TARGET_COUNTIES)
    crashes = crashes[county_mask].copy()

    crashes["sev_weight"] = crashes["KABCO_Severity"].apply(weight_by_kabco)

    return crashes.to_crs(32616)


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return crash_norm in [0, 1] indexed by segment_id."""
    crashes_m = _load_crashes()
    segs_m = segments.to_crs(32616).copy()

    buf_gdf = segs_m.set_geometry(segs_m.geometry.buffer(_BUFFER_M))
    joined = gpd.sjoin(
        buf_gdf[["segment_id", "geometry"]],
        crashes_m[["geometry", "sev_weight"]],
        how="left",
        predicate="contains",
    )

    agg = joined.groupby("segment_id")["sev_weight"].sum().fillna(0.0)
    result = agg.reindex(segments["segment_id"], fill_value=0.0)

    if result.max() == 0.0:
        # Null policy: no crashes in corridor → return all zeros
        return pd.Series(0.0, index=segments["segment_id"], dtype=float)

    return normalize(result).clip(0.0, 1.0)
