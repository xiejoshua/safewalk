"""flooding.py — flooding factor module.

Data source: FEMA National Flood Hazard Layer (NFHL) REST API
             Layer 28 — Special Flood Hazard Areas (SFHA)
             Queried for Clayton County / Gillem Corridor bbox at runtime.

Method: Fetch SFHA polygons, intersect with segment geometries, return True
        for any segment that overlaps a flood zone.

NOTE: NFHL captures riverine and coastal flood zones (100-year/500-year) but
      does NOT capture urban pluvial (flash) flooding from impervious surface
      runoff — the primary flood risk for Atlanta-area pedestrians after heavy
      rain. Treat this as a directional signal only; False does not mean no
      flood risk.

Null policy: API unreachable or empty response → all False
             (unknown flood risk treated conservatively as no recorded zone;
             callers should weight flooding as supplemental, not blocking)
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

# Clayton County bounds: (min_lon, min_lat, max_lon, max_lat)
_CLAYTON_BBOX = "-84.5,33.5,-84.2,33.8"

_NFHL_URL = (
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
    f"?geometry={_CLAYTON_BBOX}"
    "&geometryType=esriGeometryEnvelope"
    "&inSR=4326"
    "&spatialRel=esriSpatialRelIntersects"
    "&outFields=*"
    "&f=geojson"
)

_TIMEOUT_S = 15


def _fetch_flood_zones() -> gpd.GeoDataFrame | None:
    """Fetch SFHA polygons from FEMA NFHL REST API. Returns None on failure."""
    try:
        import httpx

        resp = httpx.get(_NFHL_URL, timeout=_TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()

        gdf = gpd.read_file(resp.text, driver="GeoJSON")
        if gdf.empty:
            logger.warning("flooding.py: NFHL returned 0 flood zone features for Clayton County bbox")
            return None

        logger.info("flooding.py: loaded %d SFHA polygons from NFHL", len(gdf))
        return gdf.to_crs(32616)

    except Exception as exc:
        logger.warning("flooding.py: NFHL API unreachable (%s); returning all False", exc)
        return None


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return flood zone membership indexed by segment_id.

    True  = segment intersects a FEMA Special Flood Hazard Area (100-yr zone)
    False = no intersection found, or API unavailable
    """
    no_flood = pd.Series(False, index=segments["segment_id"], dtype=bool)

    flood_zones = _fetch_flood_zones()
    if flood_zones is None:
        return no_flood

    segs_m = segments.to_crs(32616).copy()

    try:
        joined = gpd.sjoin(
            segs_m[["segment_id", "geometry"]],
            flood_zones[["geometry"]],
            how="left",
            predicate="intersects",
        )
        in_flood = joined.groupby("segment_id")["index_right"].any()
        result = in_flood.reindex(segments["segment_id"], fill_value=False)
        logger.info("flooding.py: %d / %d segments intersect a flood zone", int(result.sum()), len(result))
        return result

    except Exception as exc:
        logger.warning("flooding.py: intersection failed (%s); returning all False", exc)
        return no_flood
