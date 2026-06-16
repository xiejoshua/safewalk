"""canopy.py — canopy_pct factor module.

Data source: Meta/WRI canopy-height model (v1 `alsgedi_global_v6_float`)
             EPSG:4326 10-degree COG tiles on AWS S3 (public, no key):
             forests/v1/alsgedi_global_v6_float_epsg4326_v3_10deg/
                 meta_chm_lat=<N-edge>_lon=<W-edge>_cover5m.tif
             `cover5m` = fraction of ground covered by canopy >= 5 m, stored as a
             per-mille integer (0-1000) at ~28 m (0.00025 deg) resolution. The
             >=5 m threshold isolates real shade canopy from ground vegetation.

             NOTE: Meta retired the old single-file `tree_height_geo_*.tif` COG;
             the dataset is now tiled. The correct 10-degree tile is derived from
             the corridor bbox at runtime.

Scoring: canopy_pct = cover5m / 1000 sampled at each segment's representative
         point (nearest pixel). At ~28 m resolution a sub-pixel 5 m buffer adds
         nothing, so we sample the point directly.

Null policy: raster unreachable, or segment outside the tile → canopy_pct = 0.0
             (unknown coverage treated as no shade, not full shade)
"""
from __future__ import annotations

import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Clayton County clip bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84
_CLAYTON_BBOX = (-84.5, 33.5, -84.2, 33.8)

# Meta cover5m is stored as a per-mille integer (0-1000); divide to get [0, 1].
_COVER_SCALE = 1000.0

_COG_BASE = (
    "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/"
    "alsgedi_global_v6_float_epsg4326_v3_10deg"
)


def _tile_url(lon: float, lat: float) -> str:
    """COG URL for the 10-degree cover5m tile containing (lon, lat).

    Tiles are named by their NW corner: lon = west edge (floor to 10),
    lat = north edge (ceil to 10). Tile spans [lon, lon+10] x [lat-10, lat].
    """
    west_edge = math.floor(lon / 10.0) * 10.0
    north_edge = math.ceil(lat / 10.0) * 10.0
    return f"{_COG_BASE}/meta_chm_lat={north_edge:.1f}_lon={west_edge:.1f}_cover5m.tif"


def _open_canopy_raster(lon: float, lat: float):
    """Open the Meta cover5m COG tile for (lon, lat) via rioxarray.

    A missing rioxarray is a deployment error and is re-raised loudly. A network
    / raster failure returns None so callers fall back to the null policy.
    """
    try:
        import rioxarray as rxr
    except ImportError as exc:
        raise RuntimeError(
            "canopy.py requires rioxarray. "
            "Run `pip install rioxarray` in the prebake environment and retry."
        ) from exc

    url = _tile_url(lon, lat)
    try:
        da = rxr.open_rasterio(url, masked=True, lock=False)
        logger.info("canopy.py: opened cover5m tile %s", url.rsplit("/", 1)[-1])
        return da
    except Exception as exc:
        logger.warning(
            "canopy.py: could not open COG tile %s (%s); returning 0.0 for all segments",
            url, exc,
        )
        return None


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return canopy_pct in [0, 1] indexed by segment_id."""
    zeros = pd.Series(0.0, index=segments["segment_id"], dtype=float)

    # Pick the tile from the corridor's centre (all segments share one 10-deg tile).
    centre_lon = (_CLAYTON_BBOX[0] + _CLAYTON_BBOX[2]) / 2.0
    centre_lat = (_CLAYTON_BBOX[1] + _CLAYTON_BBOX[3]) / 2.0
    raster = _open_canopy_raster(centre_lon, centre_lat)
    if raster is None:
        logger.warning("canopy.py: offline fallback — all segments get canopy_pct = 0.0")
        return zeros

    try:
        import xarray as xr

        # Clip to the corridor bbox so the read is small, then sample each segment's
        # representative point at the nearest pixel.
        clip = raster.rio.clip_box(*_CLAYTON_BBOX).squeeze(drop=True)

        pts = segments.to_crs(4326).geometry.representative_point()
        xs = xr.DataArray(pts.x.to_numpy(), dims="seg")
        ys = xr.DataArray(pts.y.to_numpy(), dims="seg")

        sampled = clip.sel(x=xs, y=ys, method="nearest").to_numpy().astype(float)
        # Null policy: pixels outside the tile / nodata → 0.0 canopy.
        sampled = np.nan_to_num(sampled, nan=0.0)
        canopy_pct = np.clip(sampled / _COVER_SCALE, 0.0, 1.0)

        result = pd.Series(canopy_pct, index=segments["segment_id"], dtype=float)
        logger.info(
            "canopy.py: sampled %d segments (mean=%.3f, max=%.3f)",
            len(result), float(result.mean()), float(result.max()),
        )
        return result

    except Exception as exc:
        logger.warning("canopy.py: raster processing failed (%s); returning 0.0", exc)
        return zeros
