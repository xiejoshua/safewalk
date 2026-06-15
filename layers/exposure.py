"""exposure.py — heat exposure factor module.

Data source: OpenMeteo Historical Weather API (ERA5 reanalysis)
             https://archive-api.open-meteo.com/v1/archive
             Free, no API key required. ERA5 grid resolution ~25 km.

Method: Fetch mean summer (June–August 2024) daily maximum temperature for the
        Gillem Corridor centroid in Clayton County, GA. ERA5 resolution (~25 km)
        exceeds the corridor width (~5 km), so all segments share the same base
        temperature reading. Spatial variation in heat within the corridor is
        captured by canopy_pct (tree shade lowers felt heat exposure).

Normalization: fixed Atlanta-area bounds
  28°C → 0.0  (comfortable summer afternoon)
  40°C → 1.0  (extreme heat event / heat advisory threshold)

Null policy: API unreachable → exposure_norm = 0.65 for all segments
             (Atlanta summers are genuinely dangerous; 0.0 would understate real risk)
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

# Gillem Corridor center (Forest Park, GA — Clayton County)
_CORRIDOR_LAT = 33.70
_CORRIDOR_LON = -84.37

# Atlanta summer heat normalization bounds (°C)
_TEMP_LO_C = 28.0   # comfortable summer afternoon → score 0.0
_TEMP_HI_C = 40.0   # extreme heat event → score 1.0

# Fallback when API is unreachable; do not return 0.0 — Atlanta is genuinely hot
_FALLBACK_SCORE = 0.65

_OPENMETEO_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
    f"?latitude={_CORRIDOR_LAT}"
    f"&longitude={_CORRIDOR_LON}"
    "&start_date=2024-06-01"
    "&end_date=2024-08-31"
    "&daily=temperature_2m_max"
    "&temperature_unit=celsius"
    "&timezone=America%2FNew_York"
)

_TIMEOUT_S = 10


def _fetch_summer_temp_c() -> float | None:
    """Return mean summer daily-max temperature (°C) from OpenMeteo ERA5 API."""
    try:
        import httpx

        resp = httpx.get(_OPENMETEO_URL, timeout=_TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()

        data = resp.json()
        temps = data.get("daily", {}).get("temperature_2m_max", [])

        valid = [t for t in temps if t is not None]
        if not valid:
            logger.warning("exposure.py: OpenMeteo returned empty temperature array")
            return None

        mean_temp = sum(valid) / len(valid)
        logger.info(
            "exposure.py: mean summer max temperature = %.1f°C (%d days sampled)",
            mean_temp, len(valid),
        )
        return mean_temp

    except Exception as exc:
        logger.warning("exposure.py: OpenMeteo API unavailable (%s); using fallback", exc)
        return None


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return heat exposure_norm in [0, 1] indexed by segment_id.

    Uses mean summer daily maximum temperature from OpenMeteo ERA5 reanalysis
    for the Gillem Corridor (Clayton County, GA). Score is uniform across all
    segments at ERA5 resolution; within-corridor heat variation is captured by
    canopy_pct in the scoring engine's weighted sum.
    """
    temp_c = _fetch_summer_temp_c()

    if temp_c is None:
        # Null policy: API unavailable → fallback (unknown but not zero exposure)
        logger.warning(
            "exposure.py: using fallback score %.2f (offline — Atlanta heat not measured, not absent)",
            _FALLBACK_SCORE,
        )
        heat_score = _FALLBACK_SCORE
    else:
        heat_score = max(0.0, min(1.0, (temp_c - _TEMP_LO_C) / (_TEMP_HI_C - _TEMP_LO_C)))

    return pd.Series(heat_score, index=segments["segment_id"], dtype=float)
