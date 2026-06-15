"""slope.py — slope_risk and barrier factor module.

Data source: backend/data/dem.tif (USGS 3DEP 1/3 arc-second DEM)
             If dem.tif is absent, all segments get slope_risk = 0.0 and
             barrier = False (offline-safe; do not block route scoring)

Scoring: grade = rise / run where rise = elevation difference between
         segment endpoints (sampled from DEM) and run = segment length in metres
         slope_risk = normalize(grade) linearly scaled so
           grade <= 5%  → 0.0 (comfortable walking)
           grade >= 8.33% → 1.0 (ADA cross-slope steepness limit → barrier)
         barrier = True when grade > 10% (effectively impassable wheelchair)

Returns a tuple (slope_risk: pd.Series, barrier: pd.Series[bool])
        both indexed by segment_id
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEM_FILE = Path(__file__).resolve().parent.parent / "backend" / "data" / "dem.tif"

# ADA-referenced grade thresholds
_GRADE_COMFORTABLE = 0.05    # 5% — no penalty
_GRADE_ADA_LIMIT = 0.0833    # 8.33% — maps to score = 1.0
_GRADE_BARRIER = 0.10        # 10% — impassable for wheelchairs


def _sample_elevation_rasterio(dem_path: Path, geom_wgs) -> float | None:
    """Sample the DEM at a Point geometry (WGS84). Returns None on failure."""
    try:
        import rasterio
        from pyproj import Transformer

        with rasterio.open(dem_path) as src:
            transformer = Transformer.from_crs("EPSG:4326", src.crs.to_epsg() or "EPSG:4326", always_xy=True)
            x, y = transformer.transform(geom_wgs.x, geom_wgs.y)
            for val in src.sample([(x, y)]):
                elev = float(val[0])
                if elev == src.nodata or np.isnan(elev):
                    return None
                return elev
    except Exception:
        return None


def _grade_from_length(segs_m: gpd.GeoDataFrame, dem_path: Path) -> pd.Series:
    """Sample DEM at segment start/end using projected segment lengths."""
    import shapely.geometry as sg

    segs_wgs = segs_m.to_crs(4326)
    grades: list[float] = []

    for idx, row in segs_m.iterrows():
        wgs_row = segs_wgs.loc[idx]
        geom_wgs = wgs_row.geometry
        run_m = row.geometry.length

        coords = list(geom_wgs.coords)
        start_pt = sg.Point(coords[0])
        end_pt = sg.Point(coords[-1])

        elev_start = _sample_elevation_rasterio(dem_path, start_pt)
        elev_end = _sample_elevation_rasterio(dem_path, end_pt)

        if elev_start is None or elev_end is None or run_m < 1.0:
            grades.append(0.0)
        else:
            grades.append(abs(elev_end - elev_start) / run_m)

    return pd.Series(grades, index=segs_m.index, dtype=float)


def score(segments: gpd.GeoDataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (slope_risk, barrier) both indexed by segment_id.

    slope_risk: float in [0, 1]
    barrier: bool (True = effectively impassable for wheelchairs)
    """
    zeros = pd.Series(0.0, index=segments["segment_id"], dtype=float)
    no_barrier = pd.Series(False, index=segments["segment_id"], dtype=bool)

    if not _DEM_FILE.exists():
        # Null policy: no DEM → unknown slope, not zero slope
        logger.warning(
            "slope.py: %s not found; returning slope_risk=0.0 (unknown, not flat)",
            _DEM_FILE,
        )
        return zeros, no_barrier

    try:
        segs_m = segments.to_crs(32616).copy()
        segs_m["segment_id"] = segments["segment_id"].values

        grades = _grade_from_length(segs_m, _DEM_FILE)
        grades.index = segments["segment_id"]

        span = _GRADE_ADA_LIMIT - _GRADE_COMFORTABLE
        slope_risk = ((grades - _GRADE_COMFORTABLE) / span).clip(0.0, 1.0)
        barrier = grades > _GRADE_BARRIER

        return slope_risk, barrier

    except Exception as exc:
        logger.warning("slope.py: DEM processing failed (%s); returning 0.0", exc)
        return zeros, no_barrier
