"""Parquet-backed segment lookups."""

from __future__ import annotations

from typing import Any

from shapely.geometry import mapping

from app.scoring import crossing_penalty, resolve_weights_from_sliders, segment_risk
from app.segments import SegmentStore

FACTOR_FIELDS = (
    "sidewalk_cov",
    "traffic_risk",
    "crash_norm",
    "hazard_norm",
    "canopy_pct",
    "exposure_norm",
    "slope_risk",
)


class SegmentRepository:
    """Read segment scores from scored_segments.parquet."""

    def __init__(self, segment_store: SegmentStore):
        self.segment_store = segment_store

    def get_segment(self, segment_id: str) -> dict[str, Any]:
        gdf = self.segment_store.gdf
        if segment_id not in gdf.index:
            raise KeyError(segment_id)

        row = gdf.loc[segment_id]
        seg = {field: row.get(field) for field in FACTOR_FIELDS}
        seg.update(
            {
                "segment_id": segment_id,
                "barrier": row.get("barrier"),
                "crossing_penalty": row.get("crossing_penalty"),
                "highway": row.get("highway"),
                "wheelchair": row.get("wheelchair"),
            }
        )
        geom = mapping(row.geometry) if row.geometry is not None else None
        return self._build_response(seg, geom)

    def _build_response(self, seg: dict[str, Any], geometry: dict | None) -> dict[str, Any]:
        # Composite score uses the light/day theme defaults — representative
        # for /segment/{id} endpoint where no user preferences are available.
        cp = crossing_penalty(seg, step_free=False)
        weights = resolve_weights_from_sliders(theme="light")
        composite = segment_risk(seg, weights, step_free=False, crossing_penalty_value=cp)

        return {
            "segment_id": seg["segment_id"],
            "sidewalk_cov": float(seg.get("sidewalk_cov") or 0.0),
            "traffic_risk": float(seg.get("traffic_risk") or 0.0),
            "crash_norm": float(seg.get("crash_norm") or 0.0),
            "hazard_norm": float(seg.get("hazard_norm") or 0.0),
            "canopy_pct": float(seg.get("canopy_pct") or 0.0),
            "exposure_norm": float(seg.get("exposure_norm") or 0.0),
            "slope_risk": float(seg.get("slope_risk") or 0.0),
            "composite_score": round(composite, 6),
            "geometry": geometry,
        }
