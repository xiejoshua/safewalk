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

import json
import logging
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
_BATCH_SIZE = 100        # OpenMeteo elevation accepts up to 100 coordinates per GET call
_COORD_PRECISION = 6     # round lat/lon for dedup + lookup keys
_TIMEOUT_S = 15

# Rate-limit handling: OpenMeteo's free tier caps around ~600 requests/minute.
# 30k segments dedupe to ~15k unique endpoints → ~150 calls cold, which can still
# trip the limit. Retry on HTTP 429 after the rate window, and cache to disk so
# re-runs are free.
_MAX_RETRIES = 4
_RATELIMIT_SLEEP_S = 60
_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "elevation_cache.json"

# ADA-referenced grade thresholds
_GRADE_COMFORTABLE = 0.05    # 5% — no penalty
_GRADE_ADA_LIMIT = 0.0833    # 8.33% — maps to score = 1.0
_GRADE_BARRIER = 0.10        # 10% — impassable for wheelchairs


def _load_cache() -> dict[tuple[float, float], float]:
    """Load the on-disk elevation cache. Returns {} if absent or unreadable."""
    if not _CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(_CACHE_FILE.read_text())
        # JSON keys are strings ("lat,lon"); rebuild the (lat, lon) tuple keys.
        return {tuple(float(p) for p in k.split(",")): float(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning("slope.py: could not read elevation cache (%s); ignoring", exc)
        return {}


def _save_cache(elevations: dict[tuple[float, float], float]) -> None:
    """Persist the elevation cache so subsequent prebake runs are free."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        serializable = {f"{lat},{lon}": v for (lat, lon), v in elevations.items()}
        _CACHE_FILE.write_text(json.dumps(serializable))
    except Exception as exc:
        logger.warning("slope.py: could not write elevation cache (%s); continuing", exc)


def _get_batch(httpx, batch: list[tuple[float, float]]) -> list | None:
    """Fetch one batch of coords, retrying on HTTP 429. Returns None on failure."""
    lats = ",".join(str(lat) for lat, _ in batch)
    lons = ",".join(str(lon) for _, lon in batch)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(
                _ELEVATION_URL,
                params={"latitude": lats, "longitude": lons},
                timeout=_TIMEOUT_S,
            )
            resp.raise_for_status()
            return resp.json().get("elevation", [])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < _MAX_RETRIES - 1:
                logger.warning(
                    "slope.py: OpenMeteo rate-limited (429); waiting %ds (attempt %d/%d)",
                    _RATELIMIT_SLEEP_S, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(_RATELIMIT_SLEEP_S)
                continue
            logger.warning("slope.py: OpenMeteo elevation request failed (%s)", exc)
            return None
        except Exception as exc:
            logger.warning("slope.py: OpenMeteo elevation request failed (%s)", exc)
            return None
    return None


def _fetch_elevations(coords: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    """Map each (lat, lon) to elevation (m) via the OpenMeteo Elevation API.

    Reads/writes a disk cache and retries on rate limits. Returns whatever it
    could resolve (possibly partial); callers apply the null policy per segment
    for any coord still missing.
    """
    try:
        import httpx
    except Exception as exc:
        logger.warning("slope.py: httpx unavailable (%s); skipping elevation lookup", exc)
        return {}

    elevations = _load_cache()
    missing = [c for c in coords if c not in elevations]
    if not missing:
        logger.info("slope.py: all %d coords served from elevation cache", len(coords))
        return elevations

    logger.info(
        "slope.py: %d/%d coords from cache, fetching %d from OpenMeteo",
        len(coords) - len(missing), len(coords), len(missing),
    )

    fetched_any = False
    for i in range(0, len(missing), _BATCH_SIZE):
        batch = missing[i : i + _BATCH_SIZE]
        values = _get_batch(httpx, batch)
        if values is None:
            break  # give up remaining batches; keep what we have (cache + this run)
        if len(values) != len(batch):
            logger.warning("slope.py: elevation count mismatch; stopping fetch")
            break
        for (lat, lon), elev in zip(batch, values):
            if elev is not None:
                elevations[(lat, lon)] = float(elev)
                fetched_any = True

    if fetched_any:
        _save_cache(elevations)

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
