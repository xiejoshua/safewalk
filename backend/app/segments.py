"""Load pre-baked segment scores and snap routes to the network."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

from app.scoring import crossing_penalty, segment_risk

logger = logging.getLogger(__name__)

SEGMENT_COLUMNS = [
    "segment_id",
    "sidewalk_cov",
    "traffic_risk",
    "crash_norm",
    "hazard_norm",
    "canopy_pct",
    "exposure_norm",
    "slope_risk",
    "barrier",
    "crossing_penalty",
    "highway",
    "wheelchair",
]


class SegmentStore:
    """In-memory scored segment network with spatial index."""

    def __init__(self, gdf: gpd.GeoDataFrame):
        self.gdf = gdf
        if self.gdf.crs is None:
            self.gdf = self.gdf.set_crs(4326)
        self._sindex = self.gdf.sindex

    @classmethod
    def from_parquet(cls, path: str | Path) -> SegmentStore:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Scored segments not found: {path}")
        gdf = gpd.read_parquet(path)
        if "segment_id" in gdf.columns:
            gdf = gdf.set_index("segment_id", drop=False)
        logger.info("Loaded %d scored segments from %s", len(gdf), path)
        return cls(gdf)

    def snap_route(
        self,
        route: LineString,
        weights: dict[str, float],
        profile: str,
        snap_tolerance_m: float = 30.0,
    ) -> list[dict]:
        """
        Match a route LineString to pre-scored segments.

        Returns segment dicts with geometry, factor columns, and computed risk.
        """
        route_gdf = gpd.GeoDataFrame(geometry=[route], crs=4326)
        route_utm = route_gdf.to_crs(32616)
        route_line_utm = route_utm.geometry.iloc[0]

        segments_utm = self.gdf.to_crs(32616)
        matched_ids: set[str] = set()
        matched: list[dict] = []

        # Sample points along the route to find nearby segments
        num_samples = max(int(route_line_utm.length / 15), 2)
        for i in range(num_samples + 1):
            frac = i / num_samples if num_samples else 0
            pt_utm = route_line_utm.interpolate(frac, normalized=True)
            pt_wgs = gpd.GeoSeries([pt_utm], crs=32616).to_crs(4326).iloc[0]

            candidates = list(self._sindex.intersection(pt_wgs.bounds))
            best_pos: int | None = None
            best_dist = snap_tolerance_m

            for pos in candidates:
                seg_utm = segments_utm.geometry.iloc[pos]
                dist = pt_utm.distance(seg_utm)
                if dist < best_dist:
                    best_dist = dist
                    best_pos = pos

            if best_pos is not None:
                seg_id = str(self.gdf.index[best_pos])
                if seg_id in matched_ids:
                    continue
                matched_ids.add(seg_id)
                row = self.gdf.loc[seg_id]
                seg_dict = {col: row.get(col) for col in SEGMENT_COLUMNS if col in row.index}
                seg_dict["geometry"] = row.geometry
                cp = crossing_penalty(seg_dict, profile)
                seg_dict["risk"] = segment_risk(seg_dict, weights, profile, crossing_penalty=cp)
                matched.append(seg_dict)

        return matched


def create_empty_store() -> SegmentStore:
    """Minimal in-memory store for bootstrapping before parquet exists."""
    import pandas as pd

    gdf = gpd.GeoDataFrame(
        {
            "segment_id": pd.Series(dtype=str),
            "sidewalk_cov": pd.Series(dtype=float),
            "traffic_risk": pd.Series(dtype=float),
            "crash_norm": pd.Series(dtype=float),
            "hazard_norm": pd.Series(dtype=float),
            "canopy_pct": pd.Series(dtype=float),
            "exposure_norm": pd.Series(dtype=float),
            "slope_risk": pd.Series(dtype=float),
            "barrier": pd.Series(dtype=bool),
            "crossing_penalty": pd.Series(dtype=float),
            "highway": pd.Series(dtype=str),
            "wheelchair": pd.Series(dtype=str),
        },
        geometry=gpd.GeoSeries([], crs=4326),
        crs=4326,
    )
    return SegmentStore(gdf)
