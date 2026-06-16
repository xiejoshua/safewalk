# Data Model: R4 Data — Hazards, Environment & Supabase

**Phase 1 output** | **Date**: 2026-06-15

---

## 1. gap_reports Table (Supabase / PostGIS)

The authoritative storage for all rider-submitted pedestrian gap reports.

```sql
create table gap_reports (
  id          bigint generated always as identity primary key,
  geom        geometry(Point, 4326) not null,
  type        text not null
                check (type in (
                  'no_sidewalk','no_crossing','broken_sidewalk',
                  'obstruction','streetlight_out','other'
                )),
  note        text,
  photo_url   text,          -- accepted but not validated in v1
  created_at  timestamptz not null default now()
);

create index gap_reports_geom_gix on gap_reports using gist (geom);
```

**Relationships**: None. Standalone table; read by both the frontend realtime
subscription and the prebake hazards module.

**Valid `type` values and hazard weights** (matches `HAZARD_W` in `hazards.py`):

| type | weight |
|---|---|
| `broken_sidewalk` | 1.0 |
| `obstruction` | 1.0 |
| `no_sidewalk` | 0.9 |
| `no_crossing` | 0.8 |
| `streetlight_out` | 0.4 |
| `other` | 0.5 |

**State transitions**: None — reports are immutable after insert (no edit/delete
for anon users).

---

## 2. Overlay Columns on scored_segments.parquet (R4-owned)

R4 adds these columns to the GeoDataFrame produced by R3's base network build.
All are floating-point in [0, 1] except `barrier` (boolean).

| Column | Type | Range | Null policy | Source module |
|---|---|---|---|---|
| `crash_norm` | float | [0, 1] | 0.0 — no crashes in 30 m buffer | `layers/crash.py` |
| `hazard_norm` | float | [0, 1] | 0.0 — no hazard within 20 m | `layers/hazards.py` |
| `canopy_pct` | float | [0, 1] | 0.0 — no valid pixels in 5 m buffer | `layers/canopy.py` |
| `exposure_norm` | float | [0, 1] | 0.0 — segment outside block group coverage | `layers/exposure.py` |
| `slope_risk` | float | [0, 1] | 0.0 — flat segment assumed if no DEM coverage | `layers/slope.py` |
| `barrier` | bool | True/False | False — unknown ≠ inaccessible | `layers/slope.py` |

**Index**: `segment_id` (string, supplied by R3). R4 modules return a `pd.Series`
indexed by `segment_id`; R3's orchestrator left-joins on this key.

**CRS**: Geometry stored in WGS84 (EPSG:4326). R4 modules reproject to
UTM 16N (EPSG:32616) internally for metric buffer/distance operations, then
return results keyed by `segment_id` (no geometry in the returned Series).

---

## 3. Hazard Point (unified schema for 311 + gap_reports)

The hazards module unions two sources into a single in-memory GeoDataFrame
before computing `hazard_norm`. Both sources conform to this schema:

| Field | Type | Description |
|---|---|---|
| `geometry` | Point (WGS84) | Location of the reported hazard |
| `type` | str | Hazard type (from the `gap_reports.type` enum) |
| `weight` | float | Derived from `HAZARD_W[type]`; computed at union time |

**Not persisted** — constructed in memory during the prebake run only.

---

## 4. Data Files Summary

| File | Owner | Format | Status |
|---|---|---|---|
| `backend/data/Crashes_2020-2024.geojson` | R4 | GeoJSON points | ✅ already present |
| `backend/data/ATL311_Service_Requests.geojson` | R4 | GeoJSON points | to add to repo |
| `backend/data/ejscreen_georgia.csv` | R4 | CSV (EJScreen v2.3 FIPS 13) | to add to repo |
| `backend/data/dem.tif` | R4 | GeoTIFF raster | to download (USGS 3DEP) |
| `backend/data/scored_segments.parquet` | R3 base / R4 overlays | GeoParquet | R3 creates; R4 adds columns |
| Meta/WRI canopy COG | R4 | Cloud COG (AWS) | accessed at runtime via rioxarray — not stored locally |
