"""node_network.py — fix walkable-graph fragmentation by noding the network.

Problem: the baked network was cut into fixed 25 m pieces per OSM way but never
*noded* at intersections, and the router only connects segments whose endpoints
coincide. Junctions land in the middle of a 25 m segment, so intersecting ways
never connect — the graph shatters into ~2,300 islands and almost nothing routes.

Fix: split each segment at every point where another segment touches/crosses it,
so junctions become shared endpoints. Each split piece inherits its parent's
scores (no merging → per-segment risk granularity is preserved). This collapses
the network to one connected component (~97%).

Operates on the already-baked parquet (seconds), so it avoids re-running the full
remote-data bake. The original is backed up to scored_segments.prenode.parquet.

Run:  python -m scripts.node_network   (from backend/)
"""
from __future__ import annotations

import shutil
from pathlib import Path

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import Point
from shapely.ops import substring

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "outputs" / "scored_segments.parquet"
BACKUP = REPO / "outputs" / "scored_segments.prenode.parquet"

UTM = 32616
MIN_PIECE_M = 0.1      # drop degenerate slivers
EDGE_GUARD_M = 0.3     # ignore split points within this of an existing endpoint


def _points_of(inter) -> list[Point]:
    """Extract candidate split points from a geometry intersection."""
    if inter.is_empty:
        return []
    kind = inter.geom_type
    if kind == "Point":
        return [inter]
    if kind == "MultiPoint":
        return list(inter.geoms)
    out: list[Point] = []
    for part in getattr(inter, "geoms", [inter]):
        if part.geom_type == "Point":
            out.append(part)
        elif part.geom_type == "LineString":  # collinear overlap → use its ends
            coords = list(part.coords)
            out += [Point(coords[0]), Point(coords[-1])]
    return out


def node_network() -> None:
    g = gpd.read_parquet(SRC)
    src_crs = g.crs
    attr_cols = [c for c in g.columns if c != "geometry"]

    gp = g.to_crs(UTM).reset_index(drop=True)
    geoms = list(gp.geometry.values)
    tree = STRtree(geoms)

    rows: list[dict] = []
    new_geoms: list = []

    for i in range(len(gp)):
        geom = geoms[i]
        length = geom.length
        base = {c: gp.iloc[i][c] for c in attr_cols}

        dists: set[float] = set()
        for j in tree.query(geom):
            if j == i:
                continue
            for pt in _points_of(geom.intersection(geoms[j])):
                d = geom.project(pt)
                if EDGE_GUARD_M < d < length - EDGE_GUARD_M:
                    dists.add(round(d, 2))

        bounds = [0.0] + sorted(dists) + [length]
        spans = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
        spans = [(a, b) for a, b in spans if b - a >= MIN_PIECE_M]

        for k, (a, b) in enumerate(spans):
            piece = substring(geom, a, b)
            if piece.is_empty or piece.length <= 0:
                continue
            rec = dict(base)
            if len(spans) > 1:
                rec["segment_id"] = f"{base['segment_id']}_{k}"
            rec["length_m"] = float(piece.length)
            rows.append(rec)
            new_geoms.append(piece)

    out = gpd.GeoDataFrame(rows, geometry=new_geoms, crs=UTM).to_crs(src_crs)
    out = out.set_index("segment_id", drop=False)
    out.index.name = "segment_id"
    out = out[[c for c in g.columns if c in out.columns]]

    if not BACKUP.exists():
        shutil.copy2(SRC, BACKUP)
        print(f"backed up original -> {BACKUP.name}")
    out.to_parquet(SRC)
    print(f"noded network: {len(g)} -> {len(out)} segments written to {SRC.name}")


if __name__ == "__main__":
    node_network()
