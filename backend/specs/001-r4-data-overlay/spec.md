# Feature Specification: R4 Data — Hazards, Environment & Supabase

**Feature Branch**: `r4/data`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "i am in charge of R4 within our design doc. create a specification for that so that you can follow it throughout the coding process"

## Clarifications

### Session 2026-06-15

- Q: What is the source file and filtering strategy for crash data? → A: Local file `backend/data/Crashes_2020-2024.geojson`. Filter to rows where `F__of_Pedestrians_per_crash > 0` (pedestrian crashes only) and `Area__County` in Fulton or DeKalb. Weight by KABCO Severity — fatal/serious injury weighted higher than minor/PDO crashes.
- Q: What is the 311 hazard source, and how is Supabase integrated? → A: Local file `backend/data/ATL311_Service_Requests.geojson` (2016–2019 Atlanta 311 reports); filter to infrastructure/physical types (sidewalk, road, obstruction). Supabase gap_reports is stubbed as an empty GeoDataFrame for now and wired up later; module must run fully offline without Supabase connectivity.
- Q: How should the canopy raster be accessed and scored? → A: Access the Meta/WRI 1 m canopy height raster as a Cloud Optimized GeoTIFF from the `dataforgood-fb-forests` AWS bucket via `rioxarray` — clip to Atlanta bbox (33.6, -84.6, 33.9, -84.2) at runtime without downloading the full raster. Score each segment as the percentage of its 5 m buffer with canopy height >= 3 m.
- Q: What is the exposure data source and formula? → A: Single source — `backend/data/ejscreen_georgia.csv` (EJScreen v2.3, Georgia block groups, state FIPS 13). Use the `PTRAF` column (traffic proximity indicator) only; no heat raster. Formula: `exposure_norm = normalize(PTRAF)` joined to segments by spatial join on block group geometry.
- Q: How should flooding be implemented? → A: Query flood zone polygons from the live FEMA NFHL REST API (`https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer`) and intersect with corridor segments. Acknowledge in code comments that NFHL misses urban flash flooding.

### Session 2026-06-15 (revision)

- Q: What geographic scope should all data modules use? → A: Clayton County only (FIPS 13063). The demo focuses on the Gillem Corridor (Forest Park / Lake City area). Fulton and DeKalb county data is not needed. All county filters updated to `{"Clayton"}`.
- Q: What should `exposure_norm` measure? → A: Actual heat exposure — mean summer (June–August) daily maximum temperature from the OpenMeteo Historical Weather API (ERA5 reanalysis, free, no key required). Normalized with fixed Atlanta bounds: 28°C = 0.0, 40°C = 1.0. The previous EP_NOVEH vehicle-access proxy is replaced entirely.
- Q: Where should the `layers/` package live? → A: Repo root (`layers/`), not `backend/layers/`. The data pipeline is separate from the FastAPI app. All module imports updated; `run_overlay.py` inserts repo root into `sys.path`.
- Q: Should `run_overlay.py` include the flooding module? → A: Yes. `flooding.score()` is included in the integration script alongside all other modules. Returns a boolean Series (`flooding` column).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Pre-Scored Safety Overlay Delivered to Scoring Engine (Priority: P1)

A rider (Marcus) requests the safest walking route from a MARTA stop to his job. The scoring
engine needs pre-computed safety data for every segment in the corridor — crash history,
nearby hazards, shade, and environmental exposure — available instantly at request time.
R4 delivers these values as columns on the walking network so the scoring engine never
performs heavy computation during a live request.

**Why this priority**: Without the overlay columns, the scoring engine has nothing to rank
routes against. This is the blocking deliverable for the entire pipeline.

**Independent Test**: Given a parquet file of corridor segments from R3, run the overlay
pipeline and verify that every segment has non-null values (or documented defaults) for
`crash_norm`, `hazard_norm`, `canopy_pct`, `exposure_norm`, `slope_risk`, and a `barrier`
flag. Load the output into the scoring engine and confirm a route score is returned.

**Acceptance Scenarios**:

1. **Given** a segment on a pedestrian-involved crash location in Clayton County,
   **When** the crash overlay runs, **Then** that segment's `crash_norm` is higher than a
   segment on a local residential street with no pedestrian crash history.
2. **Given** a segment where an ATL311 "broken sidewalk" report exists within 20 meters,
   **When** the hazards overlay runs, **Then** that segment's `hazard_norm` reflects the
   penalty for that specific hazard — regardless of whether neighboring segments have
   more or fewer reports.
3. **Given** a segment under dense tree canopy (>= 3 m height in >= 50% of its 5 m buffer),
   **When** the canopy overlay runs, **Then** that segment's `canopy_pct` is materially
   higher than a segment on open exposed pavement.
4. **Given** a segment where no hazard reports exist within 20 meters, **When** the overlay
   runs, **Then** `hazard_norm` is 0 — silence does not imply either safety or danger.
5. **Given** a segment with grade > 10%, **When** the overlay runs, **Then** the
   `barrier` flag is set to true and the segment is excluded when the accessible
   profile is active.
6. **Given** Supabase is unavailable or not configured, **When** the hazards module runs,
   **Then** it completes successfully using only the local ATL311 data (Supabase gap_reports
   stubbed as empty).

---

### User Story 2 — Rider Submits a Gap Report and It Appears Live (Priority: P1)

Marcus encounters a missing sidewalk on his walk and wants to report it. He taps "Report
a gap" in the app, selects "No sidewalk," and submits. Within seconds, a pin appears on
the map for everyone currently viewing the corridor — and city planner Dana can see it
on the dashboard. The report will also feed into the hazards score for that segment in
future prebake runs.

**Why this priority**: Live gap reporting is the crowdsourcing demo moment — it shows
the city handoff story and is a key part of the demo script. It also makes the
`hazard_norm` column live-updatable post-bake.

**Independent Test**: Submit a gap report via the app UI. Verify the pin appears on
the map for a second browser session viewing the same corridor within 5 seconds, without
refreshing. Verify the report is stored and retrievable.

**Acceptance Scenarios**:

1. **Given** the map is open in two browsers, **When** a gap report is submitted in
   browser A, **Then** a pin appears in browser B within 5 seconds without a page refresh.
2. **Given** a rider submits a gap report with type "No sidewalk" and a note,
   **When** the submission completes, **Then** the report is stored with its location,
   type, note, and timestamp.
3. **Given** an anonymous rider (no account), **When** they attempt to submit a gap
   report, **Then** the submission succeeds — no login is required.
4. **Given** a gap report exists in Supabase, **When** the hazards overlay runs for a
   future prebake (after Supabase integration is wired up), **Then** that report is
   included in the `hazard_norm` calculation for the nearest segment, weighted
   identically to an ATL311 report of the same type.

---

### User Story 3 — City Planner Views the Gap Dashboard (Priority: P2)

Dana, a Vision Zero analyst, opens the Safewalk dashboard and sees a live map of all
reported pedestrian gaps across the corridor. She can see where riders have flagged
missing sidewalks, broken infrastructure, and unsafe crossings — giving her an evidence
base to prioritize capital improvements.

**Why this priority**: The dashboard is the "city handoff" story that anchors the Impact
judging criterion (30%). It depends on US2 being live.

**Independent Test**: With several gap reports in the database, open the dashboard view
and verify all existing pins are displayed on the map, their type labels are correct, and
new reports added during the session appear without reload.

**Acceptance Scenarios**:

1. **Given** gap reports of multiple types exist, **When** Dana opens the dashboard,
   **Then** each report appears as a pin at its reported location with a label matching
   its hazard type.
2. **Given** a new gap report is submitted while Dana has the dashboard open, **When**
   the report is saved, **Then** the new pin appears on Dana's map without requiring a
   page refresh.

---

### Edge Cases

- What happens when no hazard data exists within 20 m of a segment? → `hazard_norm` = 0.0
  (explicitly documented; absence of reports is neutral, not safe).
- What happens when canopy raster data returns no valid pixels for a segment's buffer?
  → `canopy_pct` = 0.0 (documented; absent data treated as no canopy, not full canopy).
- What happens when crash data has no pedestrian records near a segment? → `crash_norm` = 0.0
  (default; the segment is not known to be dangerous, not assumed safe).
- What happens when `kerb` or `wheelchair` tags are missing from OSM for a segment? →
  treat as "unknown," do NOT assume "no ramp" — do not over-block accessible routes.
- What if a gap report is submitted with coordinates outside the corridor? → accepted and
  stored; the hazards module will find no segments within 20 meters during prebake.
- What if the FEMA NFHL REST API is unreachable during the prebake? → flooding module
  logs a warning and returns an empty Series (all segments get no flooding penalty).
  This does not block core overlay delivery (flooding is a stretch factor).
- What if Supabase is unavailable or not yet configured? → hazards module uses only ATL311
  local data; gap_reports contribution is stubbed as an empty GeoDataFrame. Module
  completes successfully without Supabase connectivity.
- What if the OpenMeteo API is unreachable during prebake? → `exposure_norm` = 0.65 for all
  segments (documented fallback; Atlanta summer heat is real regardless of API availability).
- What happens when the ATL311 file contains report types not in the known hazard weight
  table? → assign weight 0.5 (the "other" default); do not fail.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The overlay pipeline MUST produce a `crash_norm` value (0–1) for every
  walking segment in the corridor, derived from pedestrian crash history. Only crashes
  where `F__of_Pedestrians_per_crash > 0` in Clayton County (Gillem Corridor demo scope)
  are used. Crashes MUST be weighted by KABCO Severity: fatal and serious injury crashes
  weighted higher than minor-injury and property-damage-only crashes.
- **FR-002**: The overlay pipeline MUST produce a `hazard_norm` value (0–1) for every
  walking segment, based on infrastructure hazard reports within 20 meters. Sources:
  (1) local file `backend/data/ATL311_Service_Requests.geojson` filtered to physical
  infrastructure types; (2) Supabase `gap_reports` (stubbed as empty GeoDataFrame until
  Supabase integration is wired up). The module MUST run fully offline without
  Supabase connectivity.
- **FR-003**: `hazard_norm` MUST be calculated as the maximum single-hazard penalty within
  20 meters of a segment, NOT a sum or count — to prevent over-penalizing well-reported
  areas. Hazard type weights: `broken_sidewalk` = 1.0, `obstruction` = 1.0,
  `no_sidewalk` = 0.9, `no_crossing` = 0.8, `other` = 0.5, `streetlight_out` = 0.4.
- **FR-004**: The hazard overlay MUST treat ATL311-sourced reports and crowdsourced gap
  reports as equal inputs, weighted by hazard type using the same weight table.
- **FR-005**: The overlay pipeline MUST produce a `canopy_pct` value (0–1) for every
  segment representing the percentage of the segment's 5 m buffer with tree canopy
  height >= 3 m, derived from the Meta/WRI 1 m canopy height COG raster (accessed via
  `rioxarray`, clipped to Atlanta bbox at runtime — no full raster download).
- **FR-006**: The overlay pipeline MUST produce an `exposure_norm` value (0–1) for every
  segment representing summer heat exposure. Method: fetch mean June–August daily maximum
  temperature for the Gillem Corridor centroid (Clayton County, GA) from the OpenMeteo
  Historical Weather API (ERA5 reanalysis, free, no API key required). Normalize using
  fixed Atlanta-area bounds: 28°C → 0.0, 40°C → 1.0. If the API is unreachable, return
  0.65 (documented fallback; Atlanta summers are hot, 0.0 would understate real risk).
- **FR-007**: The overlay pipeline MUST produce a `slope_risk` value (0–1) for every
  segment, normalized so that flat segments score 0 and grades at or above the
  accessibility-impairing threshold score 1.
- **FR-008**: The overlay pipeline MUST set a boolean `barrier` flag to true on any
  segment that has grade > 10%, contains steps, or is tagged as wheelchair-inaccessible.
  These segments MUST be excluded entirely from the accessible routing profile.
- **FR-009**: Missing source data for any factor MUST be filled with a documented default
  (not silently zeroed unless zero is the correct documented default). The null-handling
  policy for each column MUST be written as an inline comment in each factor module.
- **FR-010**: The gap-reporting storage system MUST accept and persist reports containing:
  geographic location, hazard type (from the defined set), optional free-text note, and
  optional photo URL.
- **FR-011**: The gap-reporting system MUST make new submissions visible to all active
  viewers within 5 seconds of submission, without requiring a page refresh.
- **FR-012**: The gap-reporting system MUST allow submissions without user authentication.
- **FR-013**: The gap-reporting system MUST support the frontend and scoring backend
  reading all stored reports; no records should be hidden from authenticated or
  unauthenticated readers.
- **FR-014** *(Stretch)*: The overlay pipeline SHOULD produce a `flooding_risk` value for
  segments that intersect FEMA flood zones, queried from the live FEMA NFHL REST API
  (`https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer`). Code MUST
  acknowledge in comments that NFHL misses urban flash flooding. Failure to reach the
  API MUST not block core overlay delivery.

### Key Entities

- **Walking Segment**: A ~25-meter piece of the pedestrian network uniquely identified by
  `segment_id`. R4 adds overlay columns to segments provided by R3; R4 does not own
  the base geometry.
- **Gap Report**: A citizen-submitted record of a pedestrian infrastructure problem,
  containing: a point location, a hazard type (from the defined set below), an optional
  note, an optional photo URL, and a creation timestamp.
  - Valid types: `no_sidewalk`, `no_crossing`, `broken_sidewalk`, `obstruction`,
    `streetlight_out`, `other`
- **ATL311 Hazard Report**: A physical-infrastructure hazard report from the local
  `ATL311_Service_Requests.geojson` file, filtered to sidewalk/road/obstruction types.
  For scoring purposes, ATL311 reports are mapped to the same hazard type vocabulary
  as gap reports and receive identical weight treatment.
- **Overlay Columns** (R4-owned output columns on the segment dataset):
  `crash_norm`, `hazard_norm`, `canopy_pct`, `exposure_norm`, `slope_risk`, `barrier`

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All six R4-owned columns (`crash_norm`, `hazard_norm`, `canopy_pct`,
  `exposure_norm`, `slope_risk`, `barrier`) are present and non-null (or documented-default)
  on every segment of the hero corridor before the Day 1 PM milestone.
- **SC-002**: A submitted gap report appears as a live pin for all concurrent viewers
  within 5 seconds of submission.
- **SC-003**: Two adjacent segments with equal physical conditions but different ATL311
  report volumes have `hazard_norm` values within 10% of each other — demonstrating that
  the density-bias mitigation works.
- **SC-004**: Every segment flagged `barrier = true` is absent from the accessible routing
  profile's candidate set in 100% of route-scoring test runs.
- **SC-005**: The null-handling policy for all six overlay columns is documented as inline
  comments in each factor module and is reviewable before demo day.
- **SC-006**: The gap-reporting system accepts anonymous submissions and returns a success
  response in under 3 seconds under normal demo conditions.
- **SC-007**: The hazards module runs to completion without error when Supabase is
  unavailable (Supabase stub returns empty GeoDataFrame; only ATL311 data used).

## Assumptions

- The base walking network (OSM segments with `segment_id`, geometry, and network
  attributes) is delivered by R3 within the first hour of the hackathon. R4 works
  against a small sample network published by R3 until the full corridor is available.
- Photo uploads are out of scope for v1. The `photo_url` field is accepted in submissions
  but not validated or stored to any cloud storage service.
- User accounts and authentication for gap reporting are out of scope. All submissions
  are anonymous.
- Flooding is a stretch deliverable. The core overlay (crash, hazards, canopy, exposure,
  slope) MUST ship; flooding is added only if time allows after core is complete.
- The canopy module accesses the Meta/WRI COG raster at runtime via `rioxarray` —
  no full raster download is needed, but prebake requires internet access for the
  raster clip. The demo itself does not require live raster access.
- `backend/data/ATL311_Service_Requests.geojson` is present before prebake runs. Note:
  ATL311 covers Atlanta city proper only — it will return no results for Clayton County
  (Gillem Corridor). Gap reports from Supabase are the primary hazard source for the corridor.
- Crash data from `backend/data/Crashes_2020-2024.geojson` is already in the repo
  and covers 2020–2024. Only pedestrian crashes (`F__of_Pedestrians_per_crash > 0`)
  in Clayton County are used for the Gillem Corridor demo.
- The exposure module uses the OpenMeteo Historical Weather API (free, no key). Prebake
  requires internet access. The offline fallback is 0.65 (documented).
- Supabase gap_reports is stubbed as an empty GeoDataFrame in hazards.py until the
  Supabase integration is wired up. This stub allows the module to run fully offline.
- The `gap_reports` table owns read access for both the frontend and the scoring backend.
  Row-level security allows anonymous insert and select for the hackathon demo.
- R4 does NOT own the `prebake.py` orchestrator — that belongs to R3. R4 provides
  factor modules with the locked interface that the orchestrator imports and calls.
- All core modules (crash, hazards, canopy, exposure, slope) MUST be runnable fully
  offline. No module may block on live network access for core functionality.
