# Research: R4 Data — Hazards, Environment & Supabase

**Phase 0 output** | **Date**: 2026-06-15

All decisions below are derived from DESIGN.md §7c–§7e, §8, and the existing
backend source code. No NEEDS CLARIFICATION markers remain.

---

## 1. Crash Data Source

**Decision**: Use `backend/data/Crashes_2020-2024.geojson` (already present in repo).

**Rationale**: The file is already committed. It covers 2020–2024 — sufficient
recency for the demo. ATLDOT HIN geometry was flagged as "unverified downloadable"
in DESIGN.md §8; the GeoJSON crash points are the fallback that is confirmed present.

**Actual schema** (confirmed from `Crashes_2020-2024.geojson`, 1,074,688 statewide features):
- Key fields: `Latitude`, `Longitude`, `F__of_Pedestrians_per_crash`, `Severity_Score`,
  `NM_Fatality`, `NM_SeriousInjury`, `KABCO_Severity`, `Crash_Year`
- Filter to pedestrian-relevant crashes: `F__of_Pedestrians_per_crash > 0` OR
  `NM_Fatality > 0` OR `NM_SeriousInjury > 0`
- Then spatial filter to the Gillem corridor bbox before any buffering

**Normalization approach**:
- Filter to pedestrian crashes within the corridor bbox first (reduces ~1M rows to dozens).
- Project crash points to UTM 16N (EPSG:32616).
- Buffer each segment by 30 m; count pedestrian crashes within buffer,
  weighted by `Severity_Score` (higher severity = more weight).
- Min-max normalize weighted counts across the corridor (0 = no crashes, 1 = corridor max).
- Null policy: segment with 0 crashes in the buffer → `crash_norm = 0.0` (documented).

**Alternatives considered**:
- ATLDOT HIN downloadable geometry — flagged unverified; not confirmed yet.
- NHTSA FARS API — fatalities only, too sparse for a single corridor.
- GDOT Numetric — requires account; skipped for hackathon speed.

---

## 2. Hazard Data Sources

**Decision**: Union of SeeClickFix Open311 JSON pull + Supabase `gap_reports`.

**Rationale**: DESIGN.md §7c specifies exactly this union. The Open311 endpoint is
keyless and confirmed live. Gap reports feed in identically so crowdsourced reports
weight the same as official reports.

**Scoring formula** (per DESIGN.md §7c, Appendix B):
```python
HAZARD_W = {
    "broken_sidewalk": 1.0, "obstruction": 1.0,
    "no_sidewalk": 0.9, "no_crossing": 0.8,
    "streetlight_out": 0.4, "other": 0.5,
}
# For each segment: max(type_weight × (1 - distance_m / 20)) over hazards within 20 m
# max-not-sum: prevents density bias (Constitution Principle II)
```

**Null policy**: No hazard within 20 m → `hazard_norm = 0.0`. Silence ≠ danger.

**Open311 endpoint**: `https://seeclickfix.com/open311/v2/requests.json` with
Atlanta bbox params. Pull at prebake time; cache to `data/open311_cache.geojson`
so the demo does not require live internet.

**Gap reports integration**: Read from Supabase using `read_postgis()` or the
Supabase Python client before computing hazards; union with Open311 points.

---

## 3. Canopy Data

**Decision**: Meta/WRI 1 m canopy height raster via rasterstats `zonal_stats`.

**Rationale**: DESIGN.md §7f lists this as Core (shade factor). Height ≥ 3 m
threshold isolates real canopy shade; NDVI and OSM trees are less reliable.
Data is CC BY 4.0 from AWS Open Data registry.

**Download path**: Meta/WRI GEE community catalog or direct AWS S3 download
for the Atlanta tile. Store as `backend/data/meta_canopy.tif`.

**Access method**: Cloud Optimized GeoTIFF accessed at runtime via `rioxarray`
from the `dataforgood-fb-forests` AWS bucket. Clip to Atlanta bbox
(33.6, -84.6, 33.9, -84.2) — no full raster download required.

**Scoring formula** *(updated by clarification 2026-06-15)*:
`canopy_pct` = percentage of the segment's 5 m buffer with canopy height >= 3 m.
Computed via `rioxarray` clip + threshold mask + fraction calculation.
This supersedes the `mean_height / 6.0` formula from DESIGN.md Appendix B.

**Null policy**: No valid pixels in buffer → `canopy_pct = 0.0` (documented).

**Alternatives considered**:
- NAIP NDVI — conflicts grass with trees; flagged in DESIGN.md §10.
- OSM trees — too sparse for reliable coverage at this granularity.

---

## 4. Exposure Data *(updated by clarification 2026-06-15)*

**Decision**: Single source — `backend/data/ejscreen_georgia.csv` (EJScreen v2.3,
Georgia block groups, FIPS 13). Use `PTRAF` column (traffic proximity indicator) only.
No heat raster needed.

**Rationale**: User clarification supersedes the DESIGN.md §7c blended formula.
EJScreen PTRAF is available as a local CSV, eliminating raster download complexity
and the NIHHIS-CAPA dependency entirely.

**Formula**:
```python
# PTRAF: percentile-based traffic proximity score at block-group level
# Normalize across all Georgia BGs, then join to segments by spatial join
exposure_norm = normalize(PTRAF)  # min-max to [0, 1]
```

**Spatial join**: Each segment assigned the `PTRAF` value of the block group it
falls within (point-in-polygon, centroid of segment).

**Null policy**: Segment not covered by any block group → `exposure_norm = 0.0`
(treated as unknown; noted in code comment; not zero exposure by assumption).

**Superseded approach**: ~~NIHHIS-CAPA heat raster + EJScreen pollution blend~~.
`data/capa_heat.tif` is NOT required.

---

## 5. Slope / Accessibility Barriers

**Decision**: USGS 3DEP DEM (1/3 arc-second or 1 m) via rasterio elevation sampling.
Mapbox Terrain-RGB is the fallback.

**Rationale**: DESIGN.md §7c specifies grade normalization 5%→0, 8.33%→1.
3DEP is free, gap-free, and confirmed available. The accessible profile uses an
earlier ramp (3%→0, 6.25%→1) — this lives inside `slope.py`, not a user slider.

**Barrier flag**: Grade > 10% → `barrier = True`. Also `highway=steps` and
`wheelchair=no` in OSM tags → `barrier = True` (R3 passes these in the network).

**Formula**:
```python
def grade_to_risk(grade: float, profile: str) -> float:
    if profile == "accessible":
        lo, hi = 0.03, 0.0625
    else:
        lo, hi = 0.05, 0.0833
    return float(min(max((grade - lo) / (hi - lo), 0.0), 1.0))
```

**Download**: USGS 3DEP via `py3dep` or direct WCS download for Gillem corridor bbox.
Store as `backend/data/dem.tif`.

---

## 6. Supabase / gap_reports

**Decision**: Supabase managed Postgres + PostGIS with anon RLS for INSERT and SELECT.
Realtime publication enabled on `gap_reports` table.

**Rationale**: DESIGN.md §7e specifies this exactly. Supabase's auto-generated REST
API means the frontend can INSERT directly without a custom backend endpoint. The
Python client (`supabase>=2.0`) lets the prebake read reports into a GeoDataFrame.

**RLS policy**:
- `anon` role: INSERT ✅, SELECT ✅ (demo simplicity; no auth required)
- No UPDATE or DELETE for anon (prevent vandalism of demo data)

**Realtime**: Enable realtime publication on `gap_reports`. Frontend subscribes to
the table channel to receive new pins without polling.

**Env vars needed**: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (prebake + frontend).

---

## 7. Python Dependencies to Add

Add to `backend/requirements.txt`:

```
rasterio>=1.3.0
rasterstats>=0.20.0
supabase>=2.0.0
```

`httpx` and `geopandas` are already present.

---

## 8. Factor Module Interface (locked with R3)

```python
# backend/layers/<factor>.py
import geopandas as gpd
import pandas as pd

def score(segments: gpd.GeoDataFrame) -> pd.Series:
    """
    Input:  GeoDataFrame with at minimum 'segment_id' (index or column)
            and 'geometry' (LineString, WGS84 / EPSG:4326).
    Output: pd.Series indexed by segment_id, float values in [0.0, 1.0].
            Missing segments: return NaN (orchestrator fills with documented default).
    """
    ...
```

R3's `prebake.py` calls each module's `score()` function and column-joins the
result on `segment_id`. R4 modules must not modify the input GeoDataFrame.
