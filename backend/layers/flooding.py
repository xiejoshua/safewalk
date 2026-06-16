"""flooding.py — flooding factor module.

Data source: FEMA National Flood Hazard Layer (NFHL) REST API
             Layer 28 — Special Flood Hazard Areas (SFHA)
             Queried for Clayton County / Gillem Corridor bbox at runtime.

Method: Fetch SFHA polygons, intersect with segment geometries, return 1.0
        for any segment that overlaps a flood zone, else 0.0.

NOTE: NFHL captures riverine and coastal flood zones (100-year/500-year) but
      does NOT capture urban pluvial (flash) flooding from impervious surface
      runoff — the primary flood risk for Atlanta-area pedestrians after heavy
      rain. Treat this as a directional signal only; False does not mean no
      flood risk.

Null policy: API unreachable or empty response → all 0.0
             (unknown flood risk treated conservatively as no recorded zone;
             callers should weight flooding as supplemental, not blocking)
"""
from __future__ import annotations

import logging
import time

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

_NFHL_QUERY = (
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
)
# NFHL caps a single query at 2000 features. A wide (county) bbox hits the cap and
# returns an arbitrary 2000 polygons that may exclude the corridor entirely, so we
# query the corridor's own bbox (well under the cap) instead.
_NFHL_MAX_RECORDS = 2000
_BBOX_PAD_DEG = 0.01  # ~1 km pad so zones straddling the edge are captured
_TIMEOUT_S = 30
_MAX_RETRIES = 4       # NFHL frequently returns transient 5xx; retry before giving up
_RETRY_SLEEP_S = 3


def _fetch_flood_zones(bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame | None:
    """Fetch SFHA polygons intersecting `bbox` (min_lon, min_lat, max_lon, max_lat).

    Retries on transient server errors / timeouts. Returns None on persistent
    failure (caller falls back to the null policy).
    """
    try:
        import httpx
    except Exception as exc:
        logger.warning("flooding.py: httpx unavailable (%s); returning all False", exc)
        return None

    params = {
        # Layer 28 (S_FLD_HAZ_AR) includes Zone X ("minimal hazard") polygons that
        # blanket the whole area; restrict to true Special Flood Hazard Areas.
        "where": "SFHA_TF='T'",
        # Round to ~1 m; NFHL's envelope parser 500s on full-precision float reprs
        # like "-84.39077230000002".
        "geometry": ",".join(f"{c:.5f}" for c in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE",
        "returnGeometry": "true",
        # Generalize geometries server-side (~11 m). Flood boundaries are detailed
        # enough that the full-resolution GeoJSON for a corridor (50 MB+) makes NFHL
        # 500 on serialization; ~11 m simplification is well within segment accuracy.
        "maxAllowableOffset": "0.0001",
        "geometryPrecision": "6",
        "f": "geojson",
    }

    text: str | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(_NFHL_QUERY, params=params, timeout=_TIMEOUT_S, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text
            break
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status is None or status >= 500
            if transient and attempt < _MAX_RETRIES - 1:
                logger.warning(
                    "flooding.py: NFHL transient error (%s); retry %d/%d in %ds",
                    exc, attempt + 1, _MAX_RETRIES, _RETRY_SLEEP_S,
                )
                time.sleep(_RETRY_SLEEP_S)
                continue
            logger.warning("flooding.py: NFHL API unreachable (%s); returning all False", exc)
            return None

    if text is None:
        return None

    try:
        gdf = gpd.read_file(text, driver="GeoJSON")
    except Exception as exc:
        logger.warning("flooding.py: could not parse NFHL response (%s); returning all False", exc)
        return None

    if gdf.empty:
        logger.warning("flooding.py: NFHL returned 0 flood zone features for corridor bbox")
        return None

    if len(gdf) >= _NFHL_MAX_RECORDS:
        logger.warning(
            "flooding.py: NFHL returned the %d-feature cap; corridor may be larger "
            "than one page — flood coverage could be incomplete", _NFHL_MAX_RECORDS,
        )

    logger.info("flooding.py: loaded %d SFHA polygons from NFHL", len(gdf))
    return gdf.to_crs(32616)


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return flood zone membership in [0, 1] indexed by segment_id.

    1.0 = segment intersects a FEMA Special Flood Hazard Area (100-yr zone)
    0.0 = no intersection found, or API unavailable
    """
    no_flood = pd.Series(0.0, index=segments["segment_id"], dtype=float)

    # Query NFHL for the corridor's own bbox (+ small pad) to stay under the
    # 2000-feature cap and get the polygons that actually cover these segments.
    minx, miny, maxx, maxy = segments.to_crs(4326).total_bounds
    bbox = (minx - _BBOX_PAD_DEG, miny - _BBOX_PAD_DEG,
            maxx + _BBOX_PAD_DEG, maxy + _BBOX_PAD_DEG)

    flood_zones = _fetch_flood_zones(bbox)
    if flood_zones is None:
        return no_flood

    # segment_id is both the index AND a column in the R3 schema. sjoin calls
    # reset_index() on the left frame, which collides with the existing column
    # ("cannot insert segment_id, already exists"). Keep it only as the index.
    segs_m = segments.to_crs(32616)[["segment_id", "geometry"]].copy()
    segs_m = segs_m.set_index("segment_id", drop=True)

    try:
        joined = gpd.sjoin(
            segs_m,
            flood_zones[["geometry"]],
            how="left",
            predicate="intersects",
        )
        # how="left" leaves index_right as NaN for non-matching segments; use
        # notna() (NaN is truthy, so a bare .any() would flag every segment).
        in_flood = joined["index_right"].notna().groupby(level=0).any()
        result = in_flood.reindex(segments["segment_id"], fill_value=False).astype(float)
        logger.info("flooding.py: %d / %d segments intersect a flood zone", int(result.sum()), len(result))
        return result

    except Exception as exc:
        logger.warning("flooding.py: intersection failed (%s); returning all 0.0", exc)
        return no_flood
