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

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "Crashes_2020-2024.geojson"
_BUFFER_M = 30.0
_TARGET_COUNTIES = {"Clayton"}
# Clayton County bbox (min_lon, min_lat, max_lon, max_lat) in WGS84. Pushed into
# read_file so we never materialize the full ~795 MB statewide GDOT feed in memory.
_CLAYTON_BBOX = (-84.5, 33.5, -84.2, 33.8)


def _load_crashes() -> gpd.GeoDataFrame:
    if not _DATA_FILE.exists():
        raise FileNotFoundError(
            f"crash.py: missing crash data at {_DATA_FILE}. "
            "Commit the Clayton-County-filtered GDOT crash GeoJSON (or run the "
            "download script) before prebake."
        )

    # Spatial pre-filter at read time keeps the load fast and memory-bounded.
    crashes = gpd.read_file(_DATA_FILE, bbox=_CLAYTON_BBOX)

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

    # segment_id is both the index AND a column in the R3 schema; sjoin's internal
    # reset_index() collides on it ("cannot insert segment_id, already exists").
    # Keep it only as the index and buffer in place.
    buf_gdf = segments.to_crs(32616)[["segment_id", "geometry"]].copy()
    buf_gdf = buf_gdf.set_index("segment_id", drop=True)
    buf_gdf["geometry"] = buf_gdf.geometry.buffer(_BUFFER_M)

    joined = gpd.sjoin(
        buf_gdf,
        crashes_m[["geometry", "sev_weight"]],
        how="left",
        predicate="contains",
    )

    agg = joined.groupby(level=0)["sev_weight"].sum()
    result = agg.reindex(segments["segment_id"], fill_value=0.0)

    # Null policy: no pedestrian crashes in corridor → normalize returns all
    # zeros (min == max), which is the documented crash_norm = 0.0 default.
    return normalize(result)
