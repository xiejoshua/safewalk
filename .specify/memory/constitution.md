<!--
SYNC IMPACT REPORT
==================
Version change:     (none) → 1.0.0  [MAJOR — initial creation]
Bump rationale:     First population of the constitution from the blank template.

New sections:       All (initial creation):
                    - Core Principles (I–VI)
                    - Tech Stack & Deployment Constraints
                    - Ethics & Bias Policy
                    - Governance

Modified principles: N/A (initial creation)
Removed sections:   N/A

Templates reviewed:
  ✅ .specify/templates/plan-template.md  — "Constitution Check" gate aligns with
       Governance requirement (check before Phase 0, re-check after Phase 1)
  ✅ .specify/templates/spec-template.md  — scope/requirements structure aligns
       with Principle IV (MVP discipline) and Principle II (equity framing)
  ✅ .specify/templates/tasks-template.md — phase structure (Setup → Foundational →
       Stories → Polish) aligns with Principle IV feature-freeze policy

Deferred TODOs:     None — all placeholders resolved from DESIGN.md
-->

# Safewalk Constitution

## Core Principles

### I. Smart-Wrapper, Not a Router

Our entire original contribution is the safety-scoring layer that re-ranks walking
routes Mapbox already returns. We MUST NOT build a routing engine from scratch.
The base map, geocoding, and turn-by-turn geometry are off-the-shelf API calls.
All original code lives in step 2 of the pipeline: snap candidate routes to
pre-scored segments and return the safest one with a plain-language explanation.

Any proposal to replace Mapbox Directions with a custom routing engine MUST be
rejected. The wrapper is reliable and demoable on Day 1; a custom router is the
primary time-sink trap in a 48-hour build.

### II. Score the Road, Not the Neighborhood

A scoring factor is legitimate only if it measures a **physical hazard at a
specific point** that endangers any pedestrian walking there. Factors that measure
social or area characteristics are prohibited without exception.

**Prohibited factors (MUST NOT be used):**
- Crime data — encodes policing bias; routes residents away from their own
  neighborhoods and reproduces redlining patterns.
- Land use / CPTED / blight / vacancy — concentrates in disinvested areas due to
  historical under-investment, not physical danger.
- Any proxy that correlates with neighborhood demographics rather than
  point-specific walkability.

**Bias mitigations on permitted factors (MUST be enforced):**
- Hazard reports (311 + crowdsourced gap reports) MUST be scored as point penalties,
  never complaint density. Silence never implies safety.
- Lighting and pollution exposure MUST be normalized within-neighborhood, not
  citywide, so affluent corridors do not pull routes toward wealthier areas.

The deliberate rejection of crime/land-use/blight data MUST be surfaced in the
pitch as a principled innovation, not treated as a silent omission.

### III. Contract-First Parallel Development

All four team roles MUST lock their inter-role contracts at kickoff before writing
any feature code. The five locked contracts are:

1. `/score` JSON request/response shape — R2 ↔ R1
2. `scored_segments.parquet` schema + `segment_id` index — R3 ↔ R2
3. Factor-module interface `score(segments) -> Series[float]` keyed by
   `segment_id`, values in [0, 1] — R3 ↔ R4
4. `gap_reports` Supabase schema + client API — R4 ↔ R1
5. The hero corridor bounding box — all roles

Each role MUST work in separate files. Contract changes require agreement from all
affected parties. Breaking a contract without team sign-off is a constitution
violation.

Development MUST proceed in parallel using stubs: R1 against mock `/score` JSON,
R2 against a stub parquet, R4 against the sample network R3 publishes in hour 1.

### IV. MVP Discipline — Feature Freeze by Day 2 Midday

Feature scope is fixed at the list below. Feature freeze is at Day 2 midday.
Surplus time after freeze goes to rehearsal and polish, not new features.

**Core (MUST ship):**
- Safe route vs. default route, per-segment risk coloring, comparison panel
- Day / night / accessible profiles (weight presets)
- Gap-report UI → Supabase insert → live pin on map
- Realtime gap dashboard

**Stretch (SHOULD if time):** shade (`canopy_pct`), exposure (`exposure_norm`),
slope (`slope_risk`), flooding

**Supplementary (MAY):** lighting proxy

Scope expansion after kickoff MUST be refused unless a Core item is explicitly
dropped first. Over-building is the highest-likelihood failure mode with four
engineers on a 48-hour clock.

### V. Data Honesty and Null Transparency

All null-handling decisions MUST be documented explicitly and defensibly. Defaults:

| Column | Missing value policy |
|---|---|
| `canopy_pct` | 0 or corridor mean — document which |
| `maxspeed` / AADT | Road-class default — document the table |
| `hazard_norm` | 0 — no reported hazard ≠ danger, ≠ safety |
| `exposure_norm` | Corridor mean |
| `kerb` / `wheelchair` | Unknown — NOT "no ramp"; do not over-block accessible routes |

Judges and city planners will ask about data quality. Every default must be
independently defensible. Undocumented assumptions are a credibility and equity
liability.

### VI. Offline-First Demo Resilience

The demo MUST run fully offline. All of the following are required before demo day:

- Pre-cache the hero corridor's Mapbox API responses and bundle the scored GeoJSON.
- Record a 60-second backup video of the complete demo flow (dangerous default →
  safe alternative → slider reroute → gap report → live pin).
- The backend MUST be capable of serving the corridor from a local snapshot with
  no live API call required.
- R1 MUST build against the mock `/score` JSON throughout development until the
  live backend is available.

Demo/WiFi failure is a medium-likelihood risk. A failed live demo destroys the
Polish score (25% of judging weight). One hour of offline prep has unlimited upside.

## Tech Stack and Deployment Constraints

The following stack is locked for the hackathon build. Changes require full team
consensus and a constitution amendment.

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Next.js + TypeScript + Mapbox GL JS + Tailwind + shadcn/ui | Zero-billing fallback: MapLibre GL JS |
| Backend | FastAPI (Python 3.12) + GeoPandas + Shapely | Docker container on Render/Railway/Fly |
| Database | Supabase / PostGIS | `gap_reports` table + realtime channel |
| Data pipeline | `prebake.py` (offline) → `scored_segments.parquet` | Runtime is spatial snap only, no heavy geo |
| Frontend hosting | Vercel | |
| CRS | UTM 16N (EPSG:32616) for buffers/distances; WGS84 (4326) for storage and serving | |

**FastAPI threading rule**: CPU-bound handlers that call GeoPandas or Shapely MUST
be declared `def` (not `async def`) so FastAPI dispatches them to a threadpool and
they never block the async event loop.

**Prebake principle**: heavy geo computation (zonal stats, buffer joins, normalization)
runs offline in `prebake.py`. Request-time work MUST be limited to a spatial snap
of the route to pre-scored segments plus a weighted sum — not raster operations.

## Ethics and Bias Policy

The equity framework from §9 of DESIGN.md is binding on all implementation decisions:

1. Only physical-hazard factors that are measurable at a specific point may be
   used in scoring. Social-area proxies are prohibited (Principle II).
2. Hazard scoring MUST use `max(type_weight × distance_decay)` over points within
   20 m of a segment — max-not-sum — to prevent complaint-density bias.
3. Crowdsourced gap reports from the `gap_reports` table MUST receive identical
   scoring treatment as SeeClickFix Open311 hazard points.
4. The `accessible` profile MUST treat grade > 10%, `highway=steps`, and
   `wheelchair=no` as hard constraints (`risk = inf`), not weighted preferences.
   Accessibility barriers are constraints, not trade-offs.
5. The bias rejection decisions (crime, land use, blight) MUST appear in the pitch
   deck as deliberate innovations, with the AJPH 2023 redlining/fatality citation
   as supporting evidence.

## Governance

This constitution supersedes all other informal team agreements. When a principle
conflicts with an implementation convenience, the principle wins unless the team
amends the constitution.

**Amendment procedure:**
- All four roles must agree.
- The version number MUST be bumped per semantic versioning:
  - MAJOR: principle removed, renamed, or fundamentally redefined.
  - MINOR: new principle or section added, or material guidance expanded.
  - PATCH: clarification, wording fix, or non-semantic refinement.
- `Last Amended` date MUST be updated to the amendment date (ISO 8601).

**Compliance gate:** Every implementation plan (`plan.md`) MUST include a
"Constitution Check" section that is resolved before Phase 0 research begins and
re-checked after Phase 1 design. Complexity violations require justification in
the `Complexity Tracking` table of the plan.

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-15
