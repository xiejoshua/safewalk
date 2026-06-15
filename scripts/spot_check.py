"""
Spot-check picker for the factor-module ground-truth validation
(TASKS.md line 60 / pitch §11).

Builds a 2500 m slice around the Gillem entrance, runs `layers/sidewalk.py`
and `layers/traffic.py`, picks 5 candidates per factor by deterministic
criteria, and emits a markdown report with Street View URLs (heading
aligned to the road bearing) plus an aerial-view fallback.

Usage:
    python3 scripts/spot_check.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Two paths: REPO root for `network`, REPO/backend for `layers` (relocated to
# backend/layers/ to align with R4's module location).
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from layers.sidewalk import score as sidewalk_score, _osm_signal, _load_arc_union  # noqa: E402
from layers.traffic import (  # noqa: E402
    score as traffic_score,
    CLASS_BASE_RISK,
    CLASS_BASE_RISK_DEFAULT,
    CLASS_SPEED_DEFAULT,
    CLASS_SPEED_DEFAULT_FALLBACK,
    _parse_maxspeed_mph,
    _speed_factor,
    _aadt_factor,
    _snap_aadt_to_ways,
    _load_aadt_stations,
)
from network.build import (  # noqa: E402
    explode_tags,
    filter_walk_eligible,
    segmentize_edges,
    slice_around,
    ways_to_gdf,
)
from network.overpass import load_cached_osm, parse_elements  # noqa: E402

OUT_MD = REPO / "data" / "spot_check.md"


def bearing_deg(line) -> float:
    """Bearing (degrees from north) of a WGS84 LineString, start to end."""
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0
    lon1, lat1 = coords[0]
    lon2, lat2 = coords[-1]
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def sv_url(lat: float, lon: float, heading: float) -> str:
    return (
        f"https://www.google.com/maps/@{lat:.6f},{lon:.6f},"
        f"3a,75y,{heading:.0f}h,90t/data=!3m1!1e3"
    )


def aerial_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@{lat:.6f},{lon:.6f},20z/data=!3m1!1e3"


def build_corpus():
    """Reproduce the 2500 m wider slice used in traffic.py smoke tests."""
    corridor = json.loads((REPO / "corridor.json").read_text())
    data = load_cached_osm(corridor["name"])
    nodes, ways = parse_elements(data)
    gdf = ways_to_gdf(ways, nodes)
    gdf = filter_walk_eligible(gdf)
    seg = segmentize_edges(gdf, target_m=25.0, min_tail_m=3.0)
    seg = explode_tags(seg)
    wide, _ = slice_around(seg, tuple(corridor["primary_destination"]["lonlat"]), radius_m=2500.0)
    return wide


def compute_components(wide: gpd.GeoDataFrame):
    """Return per-segment Series for AADT (raw), AADT factor, and OSM sidewalk signal."""
    # AADT (raw vehicles per day) via the same snap used in traffic.score
    aadt_raw = _snap_aadt_to_ways(wide, _load_aadt_stations())
    # OSM signal per segment for sidewalk
    osm_signals = wide.apply(_osm_signal, axis=1)
    return aadt_raw, osm_signals


def arc_frac_for_segments(wide: gpd.GeoDataFrame) -> pd.Series:
    """Per-segment ARC coverage fraction (without OSM combination)."""
    arc_buf = _load_arc_union()
    segs_utm = wide.to_crs("EPSG:32616")
    fracs = []
    for geom, L in zip(segs_utm.geometry.values, segs_utm.geometry.length.values):
        if L <= 0:
            fracs.append(0.0)
            continue
        inter = geom.intersection(arc_buf)
        fracs.append(min(max(inter.length / L if not inter.is_empty else 0.0, 0.0), 1.0))
    return pd.Series(fracs, index=wide.index, dtype=float)


def pick_sidewalk_candidates(wide, sw_scores, arc_frac, osm_signals):
    """Five sidewalk picks by deterministic criteria."""
    picks = []

    # 1. Pedestrian-only path — pick the longest footway/path/pedestrian/steps with score >= 0.9
    ped = wide[wide.highway.isin(["footway", "path", "pedestrian", "steps"])].copy()
    ped["sw"] = sw_scores.loc[ped.index]
    ped = ped[ped["sw"] >= 0.9].sort_values("length_m", ascending=False)
    if len(ped):
        picks.append(("Pedestrian-only path (OSM=yes floor)", ped.iloc[0].name))

    # 2. Anvil Block Rd — the Marcus walk
    anvil = wide[wide.name == "Anvil Block Road"].copy()
    if len(anvil):
        # Pick the longest segment (most representative)
        picks.append(("Anvil Block Rd (Marcus walk per DESIGN.md §11)",
                      anvil.sort_values("length_m", ascending=False).iloc[0].name))

    # 3. Service road inside warehouse — service class, sw=0, deepest in the warehouse
    # (warehouse entrance is at -84.3289, 33.6202; pick something east+south of that)
    svc = wide[wide.highway == "service"].copy()
    svc["sw"] = sw_scores.loc[svc.index]
    svc = svc[svc["sw"] == 0.0]
    if len(svc):
        # Pick one well inside the warehouse — east-most service road
        svc["centroid_lon"] = svc.geometry.centroid.x
        picks.append(("Service road inside warehouse (expect 0.0)",
                      svc.sort_values("centroid_lon", ascending=False).iloc[0].name))

    # 4. Residential with OSM=yes signal
    res = wide[wide.highway == "residential"].copy()
    res["osm"] = osm_signals.loc[res.index]
    res["sw"] = sw_scores.loc[res.index]
    res_yes = res[res["osm"] == "yes"].sort_values("length_m", ascending=False)
    if len(res_yes):
        picks.append(("Residential with OSM=yes (validates OSM-prior upgrade)",
                      res_yes.iloc[0].name))
    else:
        # Fallback: any residential with sw > 0.5
        fallback = res[res["sw"] > 0.5].sort_values("length_m", ascending=False)
        if len(fallback):
            picks.append(("Residential with high sidewalk_cov (fallback for OSM=yes)",
                          fallback.iloc[0].name))

    # 5. OSM=unknown but arc_frac > 0.3 — the cross-over case
    cross = wide.assign(osm=osm_signals, af=arc_frac, sw=sw_scores)
    cross = cross[(cross["osm"] == "unknown") & (cross["af"] > 0.3)]
    cross = cross.sort_values("af", ascending=False)
    if len(cross):
        picks.append(("OSM=unknown but ARC says yes (validates ARC > silence)",
                      cross.iloc[0].name))

    return picks[:5]


def pick_traffic_candidates(wide, tr_scores, aadt_raw):
    """Five traffic picks by deterministic criteria."""
    picks = []

    # 1. Anvil Block Rd — same way as sidewalk #2 for cross-factor comparison
    anvil = wide[wide.name == "Anvil Block Road"].copy()
    anvil["tr"] = tr_scores.loc[anvil.index]
    if len(anvil):
        # Pick the highest-traffic_risk segment
        picks.append(("Anvil Block Rd (Marcus walk; should reflect truck arterial)",
                      anvil.sort_values("tr", ascending=False).iloc[0].name))

    # 2. Jonesboro Road — primary, highest AADT in corridor
    jb = wide[wide.name == "Jonesboro Road"].copy()
    jb["aadt"] = aadt_raw.loc[jb.index]
    jb = jb.dropna(subset=["aadt"]).sort_values("aadt", ascending=False)
    if len(jb):
        picks.append(("Jonesboro Road (primary + AADT 27k; expect ≥0.85)",
                      jb.iloc[0].name))

    # 3. Forest Parkway — secondary, high AADT
    fp = wide[wide.name == "Forest Parkway"].copy()
    fp["aadt"] = aadt_raw.loc[fp.index]
    fp = fp.dropna(subset=["aadt"]).sort_values("aadt", ascending=False)
    if len(fp):
        picks.append(("Forest Parkway (secondary + AADT 18k; expect 0.6-0.8)",
                      fp.iloc[0].name))

    # 4. Quiet residential, no AADT — pick a residential with traffic_risk between 0.1 and 0.2
    res = wide[wide.highway == "residential"].copy()
    res["tr"] = tr_scores.loc[res.index]
    res["aadt"] = aadt_raw.loc[res.index]
    res_quiet = res[res["aadt"].isna() & (res["tr"].between(0.10, 0.25))]
    res_quiet = res_quiet.sort_values("length_m", ascending=False)
    if len(res_quiet):
        picks.append(("Quiet residential, no AADT (expect 0.1-0.2 from class default)",
                      res_quiet.iloc[0].name))

    # 5. A footway — pedestrian-only, expect 0.0
    ft = wide[wide.highway.isin(["footway", "path"])].copy()
    ft["tr"] = tr_scores.loc[ft.index]
    if len(ft):
        picks.append(("Footway (pedestrian-only; expect 0.0)",
                      ft.sort_values("length_m", ascending=False).iloc[0].name))

    return picks[:5]


def render_row_sidewalk(rank, reason, seg_id, wide, sw_scores, arc_frac, osm_signals):
    row = wide.loc[seg_id]
    centroid = row.geometry.centroid
    lon, lat = centroid.x, centroid.y
    heading = bearing_deg(row.geometry)
    return {
        "rank": rank,
        "reason": reason,
        "segment_id": seg_id,
        "osm_way_id": int(row.osm_way_id),
        "name": row.get("name") or "(unnamed)",
        "highway": row.highway,
        "score": f"{sw_scores.loc[seg_id]:.3f}",
        "arc_frac": f"{arc_frac.loc[seg_id]:.3f}",
        "osm_signal": osm_signals.loc[seg_id],
        "lat": lat, "lon": lon, "heading": heading,
        "length_m": f"{row.length_m:.1f}",
        "sv": sv_url(lat, lon, heading),
        "aerial": aerial_url(lat, lon),
    }


def render_row_traffic(rank, reason, seg_id, wide, tr_scores, aadt_raw):
    row = wide.loc[seg_id]
    centroid = row.geometry.centroid
    lon, lat = centroid.x, centroid.y
    heading = bearing_deg(row.geometry)
    hw = row.highway
    parsed_mph = _parse_maxspeed_mph(row.get("maxspeed"))
    speed_comp = _speed_factor(parsed_mph, hw)
    class_comp = CLASS_BASE_RISK.get(hw, CLASS_BASE_RISK_DEFAULT)
    aadt_val = aadt_raw.loc[seg_id]
    aadt_comp = _aadt_factor(aadt_val) if pd.notna(aadt_val) else None
    aadt_display = f"{aadt_val:,.0f}" if pd.notna(aadt_val) else "(none in 50m)"
    aadt_comp_display = f"{aadt_comp:.3f}" if aadt_comp is not None else "class default"
    return {
        "rank": rank,
        "reason": reason,
        "segment_id": seg_id,
        "osm_way_id": int(row.osm_way_id),
        "name": row.get("name") or "(unnamed)",
        "highway": hw,
        "score": f"{tr_scores.loc[seg_id]:.3f}",
        "class": f"{class_comp:.2f}",
        "speed_mph": f"{parsed_mph:.0f}" if parsed_mph is not None else "—",
        "speed_comp": f"{speed_comp:.2f}",
        "aadt": aadt_display,
        "aadt_comp": aadt_comp_display,
        "lat": lat, "lon": lon, "heading": heading,
        "length_m": f"{row.length_m:.1f}",
        "sv": sv_url(lat, lon, heading),
        "aerial": aerial_url(lat, lon),
    }


def format_md(sw_rows, tr_rows) -> str:
    lines = []
    lines.append("# Spot-check candidates — 2026-06-15")
    lines.append("")
    lines.append("Picked by `scripts/spot_check.py`. Each Street View URL has the camera")
    lines.append("heading set to the segment's bearing (looking down the road).")
    lines.append("")
    lines.append("## Sidewalk picks")
    lines.append("")
    for r in sw_rows:
        lines.append(f"### sidewalk #{r['rank']} — {r['reason']}")
        lines.append("")
        lines.append(f"- `segment_id`: `{r['segment_id']}`   `osm_way_id`: `{r['osm_way_id']}`")
        lines.append(f"- name / highway: **{r['name']}** / `{r['highway']}`   length: {r['length_m']} m")
        lines.append(f"- algorithm score: **{r['score']}**   arc_frac: {r['arc_frac']}   OSM signal: `{r['osm_signal']}`")
        lines.append(f"- centroid: ({r['lat']:.5f}, {r['lon']:.5f})   heading: {r['heading']:.0f}°")
        lines.append(f"- Street View: {r['sv']}")
        lines.append(f"- Aerial fallback: {r['aerial']}")
        lines.append("")

    lines.append("## Traffic picks")
    lines.append("")
    for r in tr_rows:
        lines.append(f"### traffic #{r['rank']} — {r['reason']}")
        lines.append("")
        lines.append(f"- `segment_id`: `{r['segment_id']}`   `osm_way_id`: `{r['osm_way_id']}`")
        lines.append(f"- name / highway: **{r['name']}** / `{r['highway']}`   length: {r['length_m']} m")
        lines.append(f"- algorithm score: **{r['score']}**")
        lines.append(f"- components: class={r['class']}   speed={r['speed_mph']} mph → {r['speed_comp']}   AADT={r['aadt']} → {r['aadt_comp']}")
        lines.append(f"- centroid: ({r['lat']:.5f}, {r['lon']:.5f})   heading: {r['heading']:.0f}°")
        lines.append(f"- Street View: {r['sv']}")
        lines.append(f"- Aerial fallback: {r['aerial']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("Building 2500 m corpus around Gillem entrance ...")
    wide = build_corpus()
    print(f"  {len(wide):,} segments")

    print("Scoring ...")
    sw_scores = sidewalk_score(wide)
    tr_scores = traffic_score(wide)

    print("Computing components for breakdown ...")
    aadt_raw, osm_signals = compute_components(wide)
    arc_frac = arc_frac_for_segments(wide)

    print("Picking candidates ...")
    sw_picks = pick_sidewalk_candidates(wide, sw_scores, arc_frac, osm_signals)
    tr_picks = pick_traffic_candidates(wide, tr_scores, aadt_raw)
    print(f"  sidewalk: {len(sw_picks)}/5  traffic: {len(tr_picks)}/5")

    sw_rows = [render_row_sidewalk(i + 1, reason, seg_id, wide, sw_scores, arc_frac, osm_signals)
               for i, (reason, seg_id) in enumerate(sw_picks)]
    tr_rows = [render_row_traffic(i + 1, reason, seg_id, wide, tr_scores, aadt_raw)
               for i, (reason, seg_id) in enumerate(tr_picks)]

    md = format_md(sw_rows, tr_rows)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md + "\n")
    print(f"\nWrote {OUT_MD}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
