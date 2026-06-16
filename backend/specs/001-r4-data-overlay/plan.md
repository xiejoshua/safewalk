# Implementation Plan: R4 Data — Hazards, Environment & Supabase

**Branch**: `r4/data` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `backend/specs/001-r4-data-overlay/spec.md`

## Summary

R4 extends the pre-baked walking-network parquet with six safety overlay columns
(`crash_norm`, `hazard_norm`, `canopy_pct`, `exposure_norm`, `slope_risk`, `barrier`)
that the R2 scoring engine consumes at request time. Each factor is a standalone
Python module in `backend/layers/` implementing `score(segments) -> Series[float]`
so R3's `prebake.py` can import and call them independently. R4 also owns the
Supabase `gap_reports` table — schema, RLS policy, realtime publication, and demo
seed data — which feeds both the frontend live map and the hazards scoring layer.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- `geopandas>=1.0`, `shapely>=2.0` — spatial join, buffering, segment-level ops
- `rioxarray>=0.15` / `rasterio>=1.3` — read the Meta/WRI canopy-height COG over HTTP (canopy)
- `pyarrow>=18.0` — GeoParquet I/O (already in requirements.txt)
- `httpx>=0.27` — OpenMeteo (heat + slope elevation) and FEMA NFHL REST pulls (already in requirements.txt)
- `supabase>=2.0` — Python client for reading gap_reports into GeoDataFrame at bake time

**Storage**:
- `backend/data/scored_segments.parquet` — output GeoParquet (R3 base + R4 overlay columns)
- `backend/data/Crashes_2020-2024.geojson` — crash point data (already present)
- `backend/data/ATL311_Service_Requests.geojson` — Atlanta 311 sidewalk reports (already present)
- Supabase managed Postgres + PostGIS — `gap_reports` table
- Remote data services, read at bake time (no local raster files): Meta/WRI 1 m
  canopy-height COG over HTTP (canopy), OpenMeteo ERA5 archive API (heat exposure),
  OpenMeteo Elevation API (slope), FEMA NFHL REST (flooding)

**Testing**: pytest (unit tests for factor modules using synthetic GeoDataFrames)

**Target Platform**: Local offline data pipeline (prebake); Supabase cloud (gap_reports)

**Project Type**: Data pipeline (factor modules) + managed database setup

**Performance Goals**: Full corridor prebake completes in under 5 minutes on a laptop

**Constraints**:
- Remote data services are read only at bake time; request-time serving uses only
  the pre-baked parquet (offline-safe). Each remote read degrades to a documented
  null default, so the bake never hard-fails without network.
- R4 modules must not import from `app/` (no circular deps with the FastAPI service)
- Factor modules are pure functions: `score(segments: GeoDataFrame) -> pd.Series`
- Must publish a sample parquet (with real R4 columns on a small subset) by hour 1

**Scale/Scope**: Gillem Logistics Center corridor (~500–1000 segments after 25 m segmentization)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Smart-Wrapper, Not a Router | ✅ Pass | R4 produces data columns only; does not touch routing logic |
| II. Score the Road, Not the Neighborhood | ✅ Pass | All factors measure physical hazards at specific points. No crime/blight/land-use. Hazards use max-not-sum to eliminate density bias. |
| III. Contract-First Parallel Development | ✅ Pass | Factor module interface locked: `score(segments) -> Series`. Gap-reports schema locked. Verify against R3 in hour 1. |
| IV. MVP Discipline | ✅ Pass | Core = crash + hazards + canopy + exposure + slope + gap_reports. Flooding = stretch only. |
| V. Data Honesty & Null Transparency | ✅ Pass | All null defaults documented in spec SC-005; must also appear as inline comments in each factor module. |
| VI. Offline-First Demo Resilience | ✅ Pass | Environmental factors read remote services at bake time only, each degrading to a documented null default; the demo serves from the pre-baked parquet with no live requests. |

No complexity violations. No Complexity Tracking table required.

## Project Structure

### Documentation (this feature)

```text
backend/specs/001-r4-data-overlay/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── factor-module.md          # Python interface for layer modules
│   └── gap-reports-supabase.md  # Supabase table schema + REST contract
├── checklists/
│   └── requirements.md  # Quality checklist (already complete ✅)
└── tasks.md             # Phase 2 output (/speckit-tasks — not yet created)
```

### Source Code

```text
backend/
├── layers/                        # R4-owned factor modules (new)
│   ├── __init__.py
│   ├── crash.py                   # crash_norm from Crashes_2020-2024.geojson (KABCO-weighted)
│   ├── hazards.py                 # hazard_norm from ATL311 ∪ gap_reports (max-not-sum)
│   ├── canopy.py                  # canopy_pct from Meta/WRI 1 m canopy-height COG (HTTP)
│   ├── exposure.py                # exposure_norm (heat) from OpenMeteo ERA5 API
│   ├── slope.py                   # slope_risk + barrier from OpenMeteo Elevation API
│   └── flooding.py                # flooding_risk from FEMA NFHL REST — stretch
├── supabase/                      # R4-owned DB setup (new)
│   ├── schema.sql                 # gap_reports table, index, RLS, realtime publication
│   └── seed.sql                   # 5-10 demo gap pins on the Gillem corridor
├── data/                          # R4 input vector files (no local rasters)
│   ├── Crashes_2020-2024.geojson  # already present ✅ — crash_norm source
│   └── ATL311_Service_Requests.geojson  # already present ✅ — hazard_norm (Atlanta 311)
├── app/                           # R2-owned — R4 does NOT modify these files
│   ├── scoring.py                 # already maps crash_norm, hazard_norm, etc.
│   └── segments.py                # already lists R4 columns in SEGMENT_COLUMNS
├── requirements.txt               # R4 adds: rasterio, rasterstats, supabase
└── scripts/
    └── generate_stub_parquet.py   # R2-owned stub; R4 replaces with real data
```

**Structure Decision**: Single `backend/` project. R4 adds `layers/` and `supabase/`
subdirectories and new data files. All other `app/` files are R2-owned — R4 must not
modify them. The layer modules integrate with R3's `prebake.py` via import, not by
modifying the orchestrator's file.
