"""hazards.py — hazard_norm factor module.

Data sources:
  1. backend/data/ATL311_Service_Requests.geojson (2016-2019 Atlanta 311)
     NOTE: ATL311 covers Atlanta city proper only. The Gillem Corridor is in
     Clayton County (Forest Park / Lake City) which is outside Atlanta city limits.
     ATL311 will return zero results for this corridor — gap_reports is the
     primary hazard source here.
  2. Supabase gap_reports table (live read when SUPABASE_URL + SUPABASE_ANON_KEY
     are set). Falls back to empty GeoDataFrame when env vars are absent (offline-safe).

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

_DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "Citizen_Requests_and_Calls__2016_to_2019_.geojson"
)
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


def _empty_hazards() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": gpd.GeoSeries([], crs=32616), "hazard_type": [], "weight": []},
        crs=32616,
    )


def _load_atl311() -> gpd.GeoDataFrame:
    """Load ATL311 sidewalk-related reports.

    Returns an empty GDF if the file is absent, lacks the expected columns, or has
    no matching rows (all expected for the Clayton County corridor — ATL311 only
    covers the City of Atlanta). Never raises so prebake never zeroes on a crash.
    """
    if not _DATA_FILE.exists():
        logger.debug("hazards.py: ATL311 file not found; returning empty")
        return _empty_hazards()

    raw = gpd.read_file(_DATA_FILE)

    required = {"RequestType", "TaskType"}
    if not required.issubset(raw.columns):
        logger.warning(
            "hazards.py: %s missing expected columns %s; returning empty",
            _DATA_FILE.name, sorted(required - set(raw.columns)),
        )
        return _empty_hazards()

    # Many ATL311 rows carry a null geometry — drop them before any spatial op.
    raw = raw[raw.geometry.notna() & ~raw.geometry.is_empty].copy()

    mask = raw["RequestType"] == "Field Services Sidewalk"
    sidewalk = raw[mask].copy()

    if sidewalk.empty:
        return _empty_hazards()

    sidewalk["hazard_type"] = sidewalk["TaskType"].map(TASK_TYPE_MAP).fillna("other")
    sidewalk["weight"] = sidewalk["hazard_type"].map(HAZARD_W).fillna(HAZARD_W["other"])

    return sidewalk[["geometry", "hazard_type", "weight"]].to_crs(32616)


def _load_gap_reports() -> gpd.GeoDataFrame:
    """Load gap_reports from Supabase.

    Falls back to an empty GeoDataFrame when SUPABASE_URL / SUPABASE_ANON_KEY are
    not set so the module stays fully offline-safe.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        # Null policy: Supabase not configured → empty (offline-safe)
        logger.debug("SUPABASE_URL/SUPABASE_ANON_KEY not set; skipping gap_reports")
        return _empty_hazards()

    try:
        from supabase import create_client
        from shapely import wkb as shapely_wkb

        sb = create_client(supabase_url, supabase_key)
        rows = sb.table("gap_reports").select("id,geom,type").execute().data

        if not rows:
            return _empty_hazards()

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
        return _empty_hazards()


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return hazard_norm in [0, 1] indexed by segment_id.

    Per-segment score = max(type_weight × (1 − dist_m / 20)) over hazards within
    20 m of the segment line, where dist_m is the true point-to-line distance
    (EPSG:32616 metres). Max-not-sum with distance decay keeps it a point penalty,
    never complaint density (Constitution Principle II / DESIGN.md Appendix B).
    """
    atl311 = _load_atl311()
    gap_reports = _load_gap_reports()

    all_hazards = pd.concat([atl311, gap_reports], ignore_index=True)
    all_hazards = gpd.GeoDataFrame(all_hazards, geometry="geometry", crs=32616)

    zeros = pd.Series(0.0, index=segments["segment_id"], dtype=float)
    if all_hazards.empty:
        # Null policy: no hazard data → all zeros
        return zeros

    segs_m = segments.to_crs(32616)[["segment_id", "geometry"]].copy()

    # Nearest hazard within 20 m of each segment line (true point-to-line distance).
    near = gpd.sjoin_nearest(
        segs_m,
        all_hazards[["geometry", "weight"]],
        how="left",
        max_distance=_HAZARD_RADIUS_M,
        distance_col="_dist",
    )

    matched = near.dropna(subset=["_dist"])
    if matched.empty:
        # Null policy: no hazard within radius → all zeros
        return zeros

    # Distance decay: score = type_weight × (1 − dist_m / 20m)
    decay = (1.0 - matched["_dist"] / _HAZARD_RADIUS_M).clip(lower=0.0)
    matched = matched.assign(_score=matched["weight"].fillna(0.0) * decay)

    # max-not-sum: strongest decayed hazard per segment (prevents density bias)
    agg = matched.groupby("segment_id")["_score"].max()
    return agg.reindex(segments["segment_id"], fill_value=0.0).clip(0.0, 1.0).rename(None)
