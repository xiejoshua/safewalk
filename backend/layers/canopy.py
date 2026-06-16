"""canopy.py — canopy_pct factor module.

Data source: Meta/WRI 1 m canopy height — Cloud Optimized GeoTIFF on AWS S3
             Accessed via rioxarray at runtime (no local download required)
             Falls back to 0.0 if the raster is unreachable (offline-safe)

Scoring: % of 5 m buffer around each segment with canopy height >= 3 m
         (height threshold isolates real shade canopy vs. ground vegetation)

Null policy: no valid pixels in 5 m buffer → canopy_pct = 0.0
             (unknown coverage treated as no shade, not full shade)
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Clayton County clip bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84
_CLAYTON_BBOX = (-84.5, 33.5, -84.2, 33.8)
_BUFFER_M = 5.0
_HEIGHT_THRESHOLD_M = 3.0

_COG_HTTP = (
    "https://dataforgood-fb-data.s3.amazonaws.com/forests/tree_height_geo_20201119_v0/"
    "tfcanopy_20201119_geo.tif"
)


def _open_canopy_raster():
    """Open Meta/WRI COG via rioxarray; returns None on failure."""
    try:
        import rioxarray as rxr

        da = rxr.open_rasterio(_COG_HTTP, masked=True, lock=False)
        return da
    except Exception as exc:
        logger.warning("canopy.py: could not open COG raster (%s); returning 0.0 for all segments", exc)
        return None


def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """Return canopy_pct in [0, 1] indexed by segment_id."""
    raster = _open_canopy_raster()

    if raster is None:
        # Null policy: raster unreachable → 0.0 (unknown, not no-shade)
        logger.warning("canopy.py: offline fallback — all segments get canopy_pct = 0.0")
        return pd.Series(0.0, index=segments["segment_id"], dtype=float)

    try:
        da = raster.rio.clip_box(*_CLAYTON_BBOX)
        data = da.squeeze().values  # (H, W)
        transform = da.rio.transform()

        segs_m = segments.to_crs(32616).copy()
        segs_m["_buf"] = segs_m.geometry.buffer(_BUFFER_M)

        from rasterio.features import geometry_mask

        bufs_wgs = segs_m.set_geometry("_buf").to_crs(4326)

        results: list[float] = []
        for _, row in bufs_wgs.iterrows():
            try:
                mask = geometry_mask(
                    [row["_buf"].__geo_interface__],
                    out_shape=data.shape,
                    transform=transform,
                    invert=True,
                )
                pixels = data[mask]
                valid = pixels[~np.isnan(pixels)]
                if len(valid) == 0:
                    # Null policy: no valid pixels → 0.0
                    results.append(0.0)
                else:
                    results.append(float((valid >= _HEIGHT_THRESHOLD_M).mean()))
            except Exception:
                results.append(0.0)

        return pd.Series(results, index=segments["segment_id"], dtype=float).clip(0.0, 1.0)

    except Exception as exc:
        logger.warning("canopy.py: raster processing failed (%s); returning 0.0", exc)
        return pd.Series(0.0, index=segments["segment_id"], dtype=float)
