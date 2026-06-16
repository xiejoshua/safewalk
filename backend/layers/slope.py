"""slope.py — slope_risk and barrier factor module.

Data source: OpenMeteo Elevation API (Copernicus GLO-90 DEM, ~90 m)
             https://api.open-meteo.com/v1/elevation
             Free, no API key required, no local raster file.
             If the API is unreachable, all segments get slope_risk = 0.0 and
             barrier = False (offline-safe; never blocks route scoring).

Scoring: grade = rise / run where rise = elevation difference between segment
         endpoints (sampled from the elevation API) and run = projected segment
         length in metres (EPSG:32616).
         slope_risk scales linearly:
           grade <= 5%    → 0.0  (comfortable walking)
           grade >= 8.33% → 1.0  (ADA running-slope limit)
         barrier = True when grade > 10% (effectively impassable for wheelchairs)

Null policy: API unreachable or an endpoint elevation is missing → grade treated
             as 0.0 (slope_risk = 0.0, barrier = False). This is the orchestrator's
             documented null default for an unknown slope — not a claim that the
             segment is flat.

Returns a tuple (slope_risk: pd.Series, barrier: pd.Series[bool]) indexed by segment_id.
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
_BATCH_SIZE = 100        # OpenMeteo elevation accepts up to 100 coordinates per call
_COORD_PRECISION = 6     # round lat/lon for dedup + lookup keys
_TIMEOUT_S = 15

# ADA-referenced grade thresholds
_GRADE_COMFORTABLE = 0.05    # 5% — no penalty
_GRADE_ADA_LIMIT = 0.0833    # 8.33% — maps to score = 1.0
_GRADE_BARRIER = 0.10        # 10% — impassable for wheelchairs


def _fetch_elevations(coords: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    """Map each (lat, lon) to elevation (m) via the OpenMeteo Elevation API.

    Returns an empty dict on any failure so callers fall back to the null policy.
    """
    try:
        import httpx
    except Exception as exc:
        logger.warning("slope.py: httpx unavailable (%s); skipping elevation lookup", exc)
        return {}

    elevations: dict[tuple[float, float], float] = {}
    for i in range(0, len(coords), _BATCH_SIZE):
        batch = coords[i : i + _BATCH_SIZE]
        lats = ",".join(str(lat) for lat, _ in batch)
        lons = ",".join(str(lon) for _, lon in batch)
        try:
            resp = httpx.get(
                _ELEVATION_URL,
                params={"latitude": lats, "longitude": lons},
                timeout=_TIMEOUT_S,
            )
            resp.raise_for_status()
            values = resp.json().get("elevation", [])
        except Exception as exc:
            logger.warning("slope.py: OpenMeteo elevation request failed (%s)", exc)
            return {}

        if len(values) != len(batch):
            logger.warning("slope.py: elevation count mismatch; skipping elevation lookup")
            return {}
        for (lat, lon), elev in zip(batch, values):
            if elev is not None:
                elevations[(lat, lon)] = float(elev)

    return elevations


def score(segments: gpd.GeoDataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (slope_risk, barrier) both indexed by segment_id.

    slope_risk: float in [0, 1]
    barrier: bool (True = effectively impassable for wheelchairs)
    """
    zeros = pd.Series(0.0, index=segments["segment_id"], dtype=float)
    no_barrier = pd.Series(False, index=segments["segment_id"], dtype=bool)

    # Endpoint coordinates (WGS84) for elevation lookup and projected run lengths.
    segs_wgs = segments.to_crs(4326)
    run_m = segments.to_crs(32616).geometry.length

    endpoints: list[tuple[tuple[float, float], tuple[float, float]]] = []
    unique_coords: set[tuple[float, float]] = set()
    for geom in segs_wgs.geometry:
        coords = list(geom.coords)
        start = (round(coords[0][1], _COORD_PRECISION), round(coords[0][0], _COORD_PRECISION))
        end = (round(coords[-1][1], _COORD_PRECISION), round(coords[-1][0], _COORD_PRECISION))
        endpoints.append((start, end))
        unique_coords.update((start, end))

    elevations = _fetch_elevations(sorted(unique_coords))
    if not elevations:
        # Null policy: no elevation data → unknown slope, scored as 0.0
        logger.warning("slope.py: no elevation data available; slope_risk=0.0 for all segments")
        return zeros, no_barrier

    grades: list[float] = []
    for (start, end), run in zip(endpoints, run_m):
        z0 = elevations.get(start)
        z1 = elevations.get(end)
        if z0 is None or z1 is None or run < 1.0:
            grades.append(0.0)
        else:
            grades.append(abs(z1 - z0) / run)

    grade_series = pd.Series(grades, index=segments["segment_id"], dtype=float)
    span = _GRADE_ADA_LIMIT - _GRADE_COMFORTABLE
    slope_risk = ((grade_series - _GRADE_COMFORTABLE) / span).clip(0.0, 1.0)
    barrier = grade_series > _GRADE_BARRIER

    return slope_risk, barrier
