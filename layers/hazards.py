"""hazards.py — hazard_norm factor module.

Data sources:
  1. backend/data/ATL311_Service_Requests.geojson (2016-2019 Atlanta 311)
     NOTE: ATL311 covers Atlanta city proper only. The Gillem Corridor is in
     Clayton County (Forest Park / Lake City) which is outside Atlanta city limits.
     ATL311 will return zero results for this corridor — gap_reports is the
     primary hazard source here.
  2. Supabase gap_reports table (live read when SUPABASE_URL + SUPABASE_KEY are set)
     Falls back to empty GeoDataFrame when env vars are absent (offline-safe).

Scoring: max(type_weight × (1 − dist_m/20m)) over hazards within 20 m
         Distance decay rewards proximity; max-not-sum prevents density bias
         in well-reported areas (Constitution Principle II, DESIGN.md Appendix B).

Null policy: no hazard within 20 m → hazard_norm = 0.0
             (silence ≠ danger, ≠ safety)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).resolve().parent.parent / "backend" / "data" / "ATL311_Service_Requests.geojson"
_HAZARD_RADIUS_M = 20.0

# ATL311 TaskType → hazard vocab mapping
# RequestType filter: 'Field Services Sidewalk' only
TASK_TYPE_MAP: dict[str, str] = {
    "Sidewalk - Report Broken": "broken_sidewalk",
    "Sidewalk - Repair/Replace Existing": "broken_sidewalk",
    "Sidewalk - Install New": "no_sidewalk",
    "Sidewalk - Report ADA Ramp Needed": "no_crossing",
    "Sidewalk - Report Debris": "obstruction",
    "Sidewalk - Remove Debris": "obstruction",
    "Remove Debris": "obstruction",
    "Sidewalk - Call Not Defined": "other",
    "Sidewalk - Clean": "other",
    "Sidewalk - Report Tree Needing Removal": "other",
    "Sidewalk - Report Graffiti": "other",
    "Sidewalk - Report Dead Animal": "other",
    "Sidewalk - Remove Dead Animal": "other",
    "Remove Dead Animal": "other",
    "Trim Vegetation": "other",
    "Remove Tree": "other",
    "Remove Weeds": "other",
    "Remove Graffiti": "other",
}

# Hazard type weights (Constitution Principle II / DESIGN.md §7c Appendix B)
HAZARD_W: dict[str, float] = {
    "broken_sidewalk": 1.0,
    "obstruction": 1.0,
    "no_sidewalk": 0.9,
    "no_crossing": 0.8,
    "other": 0.5,
    "streetlight_out": 0.4,
}


def _load_atl311() -> gpd.GeoDataFrame:
    """Load ATL311 sidewalk-related reports.

    Returns empty GDF if file absent or no matching rows (expected for Clayton County).
    """
    if not _DATA_FILE.exists():
        logger.debug("hazards.py: ATL311 file not found; returning empty")
        return gpd.GeoDataFrame(
            {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
            crs=32616,
        )

    raw = gpd.read_file(_DATA_FILE)
    mask = raw["RequestType"] == "Field Services Sidewalk"
    sidewalk = raw[mask].copy()

    if sidewalk.empty:
        return gpd.GeoDataFrame(
            {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
            crs=32616,
        )

    sidewalk["hazard_type"] = sidewalk["TaskType"].map(TASK_TYPE_MAP).fillna("other")
    sidewalk["weight"] = sidewalk["hazard_type"].map(HAZARD_W).fillna(HAZARD_W["other"])

    return sidewalk[["geometry", "hazard_type", "weight"]].to_crs(32616)


def _load_gap_reports() -> gpd.GeoDataFrame:
    """Load gap_reports from Supabase.

    Falls back to an empty GeoDataFrame when SUPABASE_URL / SUPABASE_KEY are
    not set so the module stays fully offline-safe.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        # Null policy: Supabase not configured → empty (offline-safe)
        logger.debug("SUPABASE_URL/SUPABASE_KEY not set; skipping gap_reports")
        return gpd.GeoDataFrame(
            {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
            crs=32616,
        )

    try:
        from supabase import create_client
        from shapely import wkb as shapely_wkb

        sb = create_client(supabase_url, supabase_key)
        rows = sb.table("gap_reports").select("id,geom,type").execute().data

        if not rows:
            return gpd.GeoDataFrame(
                {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
                crs=32616,
            )

        geoms = []
        hazard_types = []
        for row in rows:
            raw_geom = row.get("geom")
            if raw_geom is None:
                continue
            try:
                geoms.append(shapely_wkb.loads(raw_geom, hex=True))
            except Exception:
                try:
                    from shapely import wkt as shapely_wkt
                    geoms.append(shapely_wkt.loads(raw_geom))
                except Exception:
                    logger.debug("gap_reports: could not parse geom for row %s", row.get("id"))
                    continue
            hazard_types.append(row.get("type", "other"))

        gdf = gpd.GeoDataFrame(
            {
                "hazard_type": hazard_types,
                "weight": [HAZARD_W.get(t, HAZARD_W["other"]) for t in hazard_types],
            },
            geometry=geoms,
            crs=4326,
        )
        logger.info("gap_reports: loaded %d live reports from Supabase", len(gdf))
        return gdf.to_crs(32616)

    except Exception as exc:
        logger.warning("gap_reports: Supabase read failed (%s); using empty fallback", exc)
        return gpd.GeoDataFrame(
            {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
            crs=32616,
        )


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return hazard_norm in [0, 1] indexed by segment_id.

    Uses max-not-sum to prevent density bias.
    """
    atl311 = _load_atl311()
    gap_reports = _load_gap_reports()

    all_hazards = pd.concat([atl311, gap_reports], ignore_index=True)
    all_hazards = gpd.GeoDataFrame(all_hazards, geometry="geometry", crs=32616)

    segs_m = segments.to_crs(32616).copy()

    if all_hazards.empty:
        # Null policy: no hazard data → all zeros
        return pd.Series(0.0, index=segments["segment_id"], dtype=float)

    # Buffer segments to capture all hazard points within 20 m
    buf_gdf = segs_m.copy().set_geometry(segs_m.geometry.buffer(_HAZARD_RADIUS_M))
    buf_gdf = buf_gdf[["segment_id", "geometry"]]

    joined = gpd.sjoin(buf_gdf, all_hazards[["geometry", "weight"]], how="left", predicate="contains")

    if joined["index_right"].isna().all():
        # Null policy: no hazard data within radius → all zeros
        return pd.Series(0.0, index=segments["segment_id"], dtype=float)

    # Distance decay: score = type_weight × (1 − dist_m / 20m)
    # Distances measured from segment centroid to each matched hazard point (EPSG:32616 metres)
    seg_centroids = segs_m.set_index("segment_id").geometry.centroid
    haz_geoms = all_hazards.geometry  # 0-based index from ignore_index=True concat

    valid = joined.dropna(subset=["index_right"]).copy()
    valid["_dist"] = [
        seg_centroids[row["segment_id"]].distance(haz_geoms.iloc[int(row["index_right"])])
        for _, row in valid.iterrows()
    ]
    valid["score"] = (
        valid["weight"].fillna(0.0) * (1 - valid["_dist"] / _HAZARD_RADIUS_M).clip(lower=0)
    )

    # max-not-sum: highest decayed score per segment (prevents density bias)
    agg = valid.groupby("segment_id")["score"].max().fillna(0.0)

    result = agg.reindex(segments["segment_id"], fill_value=0.0)
    return result.clip(0.0, 1.0).rename(None)
