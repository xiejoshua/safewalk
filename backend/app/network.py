"""Walkable network graph and Dijkstra routing."""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, mapping
from shapely.strtree import STRtree

from app.scoring import crossing_penalty, resolve_weights_from_sliders, segment_risk
from app.segments import SEGMENT_COLUMNS, SegmentStore

logger = logging.getLogger(__name__)

DISPLAY_WEIGHTS = resolve_weights_from_sliders(theme="light")

# Walkable OSM `highway` classes. Includes arterials (primary/secondary) because
# pedestrians DO walk on them — that's the Marcus / Gillem story per DESIGN.md §11.
# Their elevated `traffic_risk` naturally penalizes them in the safe-route
# Dijkstra; the fast-route Dijkstra still uses them when they're the shortest.
# Earlier exclusion of `primary` fragmented the graph into many islands
# (largest connected component was only ~10% of nodes), making most realistic
# Gillem-area OD pairs unroutable.
ALLOWED_HIGHWAYS = frozenset({
    "footway", "path", "pedestrian", "steps",
    "residential", "living_street", "service", "unclassified",
    "tertiary", "tertiary_link",
    "secondary", "secondary_link",
    "primary", "primary_link",
})

# Pedestrians are legally barred from motorways/trunks. Hard exclude.
FORBIDDEN_HIGHWAYS = frozenset({"motorway", "motorway_link", "trunk", "trunk_link"})
SNAP_MAX_M = 300.0
NODE_PRECISION_M = 0.5


def _node_key(pt) -> tuple[float, float]:
    return (round(pt.x / NODE_PRECISION_M) * NODE_PRECISION_M, round(pt.y / NODE_PRECISION_M) * NODE_PRECISION_M)


def _tag_str(row: Any, key: str) -> str:
    val = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip().lower()


def is_walkable(row: Any) -> bool:
    highway = _tag_str(row, "highway")
    if highway in FORBIDDEN_HIGHWAYS:
        return False
    if not highway or highway not in ALLOWED_HIGHWAYS:
        return False

    foot = _tag_str(row, "foot")
    if foot == "no":
        return False

    access = _tag_str(row, "access")
    if access in {"no", "private"}:
        return False

    return True


def row_to_dict(row: Any) -> dict[str, Any]:
    seg_dict = {col: row.get(col) for col in SEGMENT_COLUMNS if col in row.index}
    seg_dict["segment_id"] = str(row.name if row.name is not None else row.get("segment_id"))
    seg_dict["geometry"] = row.geometry
    if "length_m" in row.index and row.get("length_m") is not None:
        seg_dict["length_m"] = float(row["length_m"])
    return seg_dict


def serialize_segment(seg: dict[str, Any], risk: float | None = None) -> dict[str, Any]:
    geom = seg.get("geometry")
    display_risk = segment_risk(
        seg,
        DISPLAY_WEIGHTS,
        step_free=False,
        crossing_penalty_value=crossing_penalty(seg, False),
    )
    payload: dict[str, Any] = {
        "segment_id": seg.get("segment_id"),
        "sidewalk_cov": float(seg.get("sidewalk_cov") or 0.0),
        "traffic_risk": float(seg.get("traffic_risk") or 0.0),
        "crash_norm": float(seg.get("crash_norm") or 0.0),
        "hazard_norm": float(seg.get("hazard_norm") or 0.0),
        "canopy_pct": float(seg.get("canopy_pct") or 0.0),
        "exposure_norm": float(seg.get("exposure_norm") or 0.0),
        "slope_risk": float(seg.get("slope_risk") or 0.0),
        "length_m": float(seg.get("length_m") or 0.0),
        "geometry": mapping(geom) if geom is not None else None,
        "display_score": round((1.0 - display_risk) * 100),
    }
    if risk is not None:
        payload["risk"] = round(risk, 6)
    return payload


@dataclass
class GraphEdge:
    target: tuple[float, float]
    segment_id: str
    safe_cost: float
    fast_cost: float


@dataclass
class GraphRouter:
    walkable_gdf: gpd.GeoDataFrame
    segments_utm: gpd.GeoDataFrame
    adjacency: dict[tuple[float, float], list[GraphEdge]] = field(default_factory=dict)
    segment_lookup: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_coords: dict[tuple[float, float], tuple[float, float]] = field(default_factory=dict)
    # Spatial index over walkable graph nodes (in UTM 32616). Built once in
    # _build_graph; snap_to_node uses nearest() instead of O(N) scan.
    _node_tree: STRtree | None = None
    _node_index_keys: list[tuple[float, float]] = field(default_factory=list)
    # Cache the last (weights, step_free) we set safe_cost for. Repeat configs
    # (e.g. same slider dots, auto-rerun after toggling stepFree back and
    # forth) become a no-op instead of an O(E) re-scan.
    _safe_cost_key: tuple | None = None

    @classmethod
    def from_segment_store(cls, store: SegmentStore) -> GraphRouter:
        gdf = store.gdf.copy()
        if gdf.empty:
            return cls(
                walkable_gdf=gdf,
                segments_utm=gdf.to_crs(32616) if not gdf.empty else gdf,
            )

        walkable_mask = gdf.apply(is_walkable, axis=1)
        walkable = gdf[walkable_mask].copy()
        if walkable.empty:
            logger.warning("No walkable segments after filtering")
            return cls(walkable_gdf=walkable, segments_utm=walkable.to_crs(32616))

        segments_utm = walkable.to_crs(32616)
        router = cls(walkable_gdf=walkable, segments_utm=segments_utm)
        router._build_graph()
        logger.info(
            "Graph ready: %d walkable segments, %d nodes",
            len(walkable),
            len(router.adjacency),
        )
        return router

    def _build_graph(self) -> None:
        for seg_id, row in self.walkable_gdf.iterrows():
            seg_id_str = str(seg_id)
            utm_geom: LineString = self.segments_utm.loc[seg_id].geometry
            if utm_geom.is_empty or utm_geom.length == 0:
                continue

            start = utm_geom.interpolate(0)
            end = utm_geom.interpolate(utm_geom.length)
            u = _node_key(start)
            v = _node_key(end)

            self.node_coords[u] = (start.x, start.y)
            self.node_coords[v] = (end.x, end.y)

            length_m = float(row.get("length_m") or utm_geom.length)
            seg_dict = row_to_dict(row)
            seg_dict["segment_id"] = seg_id_str
            seg_dict["length_m"] = length_m
            self.segment_lookup[seg_id_str] = seg_dict

            self.adjacency.setdefault(u, []).append(GraphEdge(v, seg_id_str, 0.0, length_m))
            self.adjacency.setdefault(v, []).append(GraphEdge(u, seg_id_str, 0.0, length_m))

        for node_edges in self.adjacency.values():
            for edge in node_edges:
                seg = self.segment_lookup[edge.segment_id]
                edge.safe_cost = 1.0
                edge.fast_cost = float(seg.get("length_m") or 1.0)

        # Spatial index over graph nodes (in UTM, same CRS the snap point gets
        # transformed into). O(log N) nearest lookups replace the per-segment
        # linear scan that dominated /route latency on the deployed graph.
        self._node_index_keys = list(self.node_coords.keys())
        self._node_tree = STRtree(
            [Point(self.node_coords[k][0], self.node_coords[k][1]) for k in self._node_index_keys]
        )

    def set_safe_costs(self, weights: dict[str, float], step_free: bool = False) -> None:
        # The slider auto-rerun fires a /route call per debounced slider tick.
        # Memoize by the exact weight + toggle config so back-to-back requests
        # with the same key short-circuit instead of re-running an O(E) scan.
        key = (tuple(sorted(weights.items())), bool(step_free))
        if key == self._safe_cost_key:
            return
        for node_edges in self.adjacency.values():
            for edge in node_edges:
                seg = self.segment_lookup[edge.segment_id]
                cp = crossing_penalty(seg, step_free)
                risk = segment_risk(seg, weights, step_free, crossing_penalty_value=cp)
                edge.safe_cost = risk if risk != float("inf") else 1e12
        self._safe_cost_key = key

    def snap_to_node(self, lon: float, lat: float) -> tuple[float, float]:
        pt_utm = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(32616).iloc[0]
        if self._node_tree is None or not self._node_index_keys:
            raise ValueError("Origin/destination is too far from the walkable network")

        # STRtree.nearest is O(log N). Returns an index into the geometries we
        # passed at build time; map it back through _node_index_keys to the
        # node key. Then validate the distance against SNAP_MAX_M — the tree
        # gives us the *nearest* node, but it might still be too far away to
        # legitimately snap.
        idx = int(self._node_tree.nearest(pt_utm))
        candidate_key = self._node_index_keys[idx]
        cx, cy = self.node_coords[candidate_key]
        if pt_utm.distance(Point(cx, cy)) > SNAP_MAX_M:
            raise ValueError("Origin/destination is too far from the walkable network")
        return candidate_key

    def dijkstra(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        cost_attr: str,
    ) -> tuple[list[str], float]:
        if start not in self.adjacency or goal not in self.adjacency:
            raise ValueError("No walkable network node near the requested location")

        dist: dict[tuple[float, float], float] = {start: 0.0}
        prev: dict[tuple[float, float], tuple[tuple[float, float], str] | None] = {start: None}
        heap: list[tuple[float, tuple[float, float]]] = [(0.0, start)]

        while heap:
            cost, node = heapq.heappop(heap)
            if cost > dist.get(node, float("inf")):
                continue
            if node == goal:
                break

            for edge in self.adjacency.get(node, []):
                edge_cost = getattr(edge, cost_attr)
                new_cost = cost + edge_cost
                if new_cost < dist.get(edge.target, float("inf")):
                    dist[edge.target] = new_cost
                    prev[edge.target] = (node, edge.segment_id)
                    heapq.heappush(heap, (new_cost, edge.target))

        if goal not in dist:
            raise ValueError("No walkable route found between origin and destination")

        segment_ids: list[str] = []
        node = goal
        while prev[node] is not None:
            parent, seg_id = prev[node]
            segment_ids.append(seg_id)
            node = parent
        segment_ids.reverse()
        return segment_ids, dist[goal]

    def route(
        self,
        origin_lon: float,
        origin_lat: float,
        dest_lon: float,
        dest_lat: float,
        weights: dict[str, float],
        step_free: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float, float, float]:
        start = self.snap_to_node(origin_lon, origin_lat)
        goal = self.snap_to_node(dest_lon, dest_lat)

        fast_ids, fast_cost = self.dijkstra(start, goal, "fast_cost")
        # Copy + attach risk so the fast (default) route can be colored by safety too.
        fast_segments: list[dict[str, Any]] = []
        for sid in fast_ids:
            seg = self.segment_lookup[sid].copy()
            cp = crossing_penalty(seg, step_free)
            seg["risk"] = segment_risk(seg, weights, step_free, crossing_penalty_value=cp)
            fast_segments.append(seg)
        fast_distance = sum(float(s.get("length_m") or 0.0) for s in fast_segments)

        self.set_safe_costs(weights, step_free)
        safe_ids, total_risk = self.dijkstra(start, goal, "safe_cost")
        safe_segments: list[dict[str, Any]] = []
        risks: list[float] = []
        for sid in safe_ids:
            seg = self.segment_lookup[sid].copy()
            cp = crossing_penalty(seg, step_free)
            risk = segment_risk(seg, weights, step_free, crossing_penalty_value=cp)
            seg["risk"] = risk
            safe_segments.append(seg)
            risks.append(risk)

        # Mean over finite-risk segments only — if step_free=True and the only
        # available path includes an unavoidable barrier, those segments report
        # inf risk. Excluding them keeps mean_risk informative about the rest
        # of the route. Callers can still detect barrier presence by checking
        # `any(s["risk"] == float("inf") for s in safe_segments)`.
        finite_risks = [r for r in risks if r != float("inf")]
        mean_risk = sum(finite_risks) / len(finite_risks) if finite_risks else float("inf")
        safe_distance = sum(float(s.get("length_m") or 0.0) for s in safe_segments)

        return safe_segments, fast_segments, mean_risk, safe_distance, fast_distance, total_risk
