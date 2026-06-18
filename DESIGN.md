# Safewalk — Engineering Design Document

**Tagline:** Safe-walk routing + crowdsourced gap-mapping that makes the *last mile to MARTA* walkable for Atlanta's transit-dependent riders — and hands the city the data to fix what's broken.

---

## 1. Executive summary

MARTA spent 2026 fixing how riders move *between* stops (Reach on-demand microtransit, the NextGen bus redesign, Better Breeze tap-to-pay). The gap that remains is the **walk to the stop**: in low-income South Atlanta, the last mile is often a sidewalk-less arterial, an unsignalized crossing, unshaded pavement, or a crash hotspot. Safewalk is a web app that routes riders on the **safest** walk between a MARTA stop and their destination (not the shortest), explains why, and — where no safe route exists — lets them photograph the gap into a live map. The original work is a multi-factor safety-scoring layer baked into a 30k-segment parquet, a Dijkstra over that scored graph, and a Gemini-vision gate on crowdsourced photos.

## 2. Problem statement

The first/last-mile problem is usually framed as **distance**. MARTA already built the vehicle answer in 2026. The unsolved problem is **walkability**: whether the last-mile distance is *traversable on foot at all*.

- **Equity geography:** MARTA serves Fulton + DeKalb (+ Clayton since 2014); Cobb and Gwinnett repeatedly opted out. Historically redlined tracts have **2.6× higher pedestrian-fatality rates** ([AJPH 2023](https://ajph.aphapublications.org/doi/10.2105/AJPH.2022.307192)).
- **Canonical failure case:** at Gillem Logistics Center, transit workers walk **1.5–2 miles along busy roadways with no sidewalk** ([Georgia Tech / ARC 2022](https://atlantaregional.org/whats-next-atl/articles/marta-reach-aims-to-address-first-mile-last-mile-issue/)).

## 3. Goals & non-goals

**Goals**
- Route a rider on the *safest* walk (multi-factor), not the shortest.
- Explain *why* a route is safer, in plain language.
- Capture rider-reported gaps into a live map usable by the city, with an AI gate so the map can't be poisoned with junk photos.

**Non-goals**
- We do **not** extend MARTA's network reach. We operate within walking range of existing stops.
- We do **not** build microtransit, fare payment, or trip-planning across modes.
- The wheelchair-accessible toggle (`step_free`) is a best-effort signal, not certified ADA routing.
- **One corridor for v1.** `prebake.py` is `--bbox`-parameterized, so citywide is a config + cache-refresh, not a rewrite — we ground-truth one corridor to keep every safety claim defensible.

## 4. Solution overview

**User flow:** pick start + destination → backend runs a safety-weighted Dijkstra over the pre-scored walk graph and returns both the safe path and the shortest path → frontend draws both colored per-segment by risk → rider tunes sliders / theme / wheelchair toggle and the route auto-reruns (750 ms debounce) → optionally photographs a gap → Gemini gates the photo → on accept, Supabase stores it and the realtime publication pushes the pin to every open map.

## 5. System architecture

```
 Browser — Next.js 15 + React 19 + MapLibre GL JS                       (Vercel)
   │  geocode (Mapbox optional → OSM Nominatim) · "draw this route" · upload photo
   │  Supabase realtime subscription → live gap pins
   ▼
 FastAPI + Shapely/GeoPandas                                            (Render, Docker)
   │  GET  /route          → Dijkstra over scored graph (safe + fast paths)
   │  POST /score          → Mapbox Directions wrapper, rank alternatives
   │  POST /analyze-gap    → Gemini vision verdict, no DB write
   │  POST /submit-gap     → re-verify + upload photo + insert row
   │  GET  /gap-reports    → list pins (newest first)
   │  PATCH /gap-reports/{id} → workflow status update
   │  GET  /segment/{id}   → per-factor breakdown for one segment
   ▼
 outputs/scored_segments.parquet   +   Supabase / PostGIS (gap_reports, realtime, gap-photos bucket)
   ▲ built offline by scripts/prebake.py
   ▲ baked into the Docker image at build time
```

The base map and geocoding are off-the-shelf. The original code is (a) the multi-factor scoring layer (`backend/layers/`), (b) the slider→sub-weight model (`backend/app/scoring.py`), (c) the safety-weighted Dijkstra over our own graph (`backend/app/network.py`), and (d) the AI-gated photo pipeline (`backend/app/gap_reports.py`).

**Deployment topology:** frontend on Vercel; FastAPI in a `python:3.12-slim` Docker image on Render, with the scored parquet baked into the image at build time (`COPY outputs/scored_segments.parquet …`); Supabase managed Postgres + Storage + Realtime.

## 6. Detailed design

### 6a. Frontend

Next.js 15 (App Router) + React 19 + TypeScript. **MapLibre GL JS** with OpenFreeMap's `liberty` style — not Mapbox GL. `@supabase/supabase-js` for the realtime channel. `framer-motion` and `lucide-react` for UI polish.

**Mapbox is optional**, used only for richer geocoding autocomplete. When `NEXT_PUBLIC_MAPBOX_TOKEN` is unset, autocomplete and reverse-geocode fall back to OSM Nominatim. The base map never needs a Mapbox token.

**Pages**
- `/` — Map + sliders + route comparison.
- `/report` — Submit a gap: photo upload → `POST /analyze-gap` (Gemini gate) → confirm category → `POST /submit-gap`.
- `/status` — Live workflow dashboard for the reports table. Realtime INSERT/UPDATE events keep it in sync.
- `/about` — Problem + solution narrative.

**Key components**
- `RealMap` (`app/components/RealMap.tsx`) — base map, start/end markers, gradient route layer (score 0..100 → red→amber→green), gap pin markers with popups, optional sidewalk-quality overlay proxied through `/api/sidewalks`.
- `PreferencePanel` (in `app/page.tsx`) — 3 sliders (5-dot snap) + wheelchair-accessible toggle.
- `RoutesPanel` — safe vs. default route comparison cards.
- `MapboxAutocomplete` — geocoding suggestions with the Nominatim fallback.

**Slider scale:** each slider has 5 discrete dots that map to 0/25/50/75/100 on the wire. The theme toggle re-applies theme defaults (light `1/2/1`, dark `0/3/1`) so the dark-mode behavior is not a no-op after a slider drag.

**Auto-rerun:** any slider / theme / wheelchair-toggle change triggers a `GET /route` after a 750 ms debounce.

**Off-corridor fallback:** if `GET /route` 404s, the frontend silently falls back to OSRM's public foot router and renders a single neutral-colored line.

### 6b. Backend API

All handlers live in `backend/app/routes.py`. Geo-CPU work runs in sync handlers so FastAPI puts it in a threadpool rather than blocking the event loop.

#### `GET /health` → `{"status": "ok"}`

#### `GET /route` — primary routing endpoint
Dijkstra over the prebaked walkable graph. Returns both the safety-weighted path and the shortest path.

Query params:
- `origin_lat`, `origin_lng`, `dest_lat`, `dest_lng` — required floats.
- `sidewalks`, `safety`, `comfort` — optional ints `[0, 100]`. Theme defaults fill any omitted slider.
- `step_free` — bool. When true, segments with `barrier == True` get `risk = inf` and the safe Dijkstra avoids them.
- `theme` — `"light"` (day defaults) or `"dark"` (night defaults).

Response:
```jsonc
{
  "safe_route": {
    "segments": [{ "segment_id": "...", "risk": 0.21, "display_score": 79, "sidewalk_cov": ..., "length_m": ..., "geometry": {...} }, ...],
    "total_risk": 0.21,
    "distance_m": 1342.8,
    "explanation": "This route prioritizes lower exposure to fast, high-volume traffic. About 64% of the path has sidewalk coverage.",
    "slider_weights": { "sidewalks": 30, "safety": 55, "comfort": 15 }
  },
  "fast_route": {
    "segments": [...],
    "distance_m": 1180.4,
    "slider_weights": { "sidewalks": 0, "safety": 0, "comfort": 0 }
  }
}
```

Origins or destinations more than `SNAP_MAX_M = 300 m` from the walkable network return `404` — the frontend reads that as "off corridor" and falls back to OSRM.

#### `POST /score` — Mapbox Directions wrapper
Body:
```jsonc
{
  "origin": [-84.347, 33.610],
  "dest":   [-84.329, 33.620],
  "sidewalks": 50, "safety": 80, "comfort": 20,
  "step_free": false,
  "theme": "light"
}
```
Fetches `default + alternatives` from Mapbox Directions (`mapbox/walking`), snaps each candidate to the pre-scored segments, scores them, sorts by score (lower = safer), returns `safest` + `alternatives`. Without a `MAPBOX_ACCESS_TOKEN`, the wrapper synthesizes a straight-line and an offset alternative.

#### `GET /gap-reports`, `POST /gap-reports`, `PATCH /gap-reports/{id}`
List (newest first), create without photo, update workflow status. PATCH body is `{"status": "reported" | "in_progress" | "processed"}`.

#### `POST /analyze-gap` (multipart)
Step 1 of the photo flow. Photo → Gemini with a structured-output schema (`is_gap`, `type`, `note`, `confidence`). No DB write. Returns `{verified: true, type, note, confidence}` or `{verified: false, reason, ai_type, confidence}`.

#### `POST /submit-gap` (multipart)
Step 2. Re-runs the Gemini verification server-side (so a client can't bypass `/analyze-gap`), uploads the photo to `gap-photos`, and inserts a row with the user-chosen category and `status='reported'`. The realtime publication pushes the new pin.

#### `POST /verify-gap` (multipart)
Back-compat single-step variant that uses Gemini's own classification when the caller doesn't supply one.

#### `GET /segment/{segment_id}`
Per-factor breakdown for a single segment.

**Confidence floor:** `analyze_gap_photo` rejects with `confidence < 0.55` even when Gemini says `is_gap=true`, so a low-confidence guess can't become a pin.

### 6c. Scoring model

**3 sliders + 1 toggle + 1 theme.** All ranges are `[0, 100]`. Light/dark theme picks default slider values; user-adjusted sliders override.

```python
SLIDER_DEFAULTS = {
    "light": {"sidewalks": 30, "safety": 55, "comfort": 15},
    "dark":  {"sidewalks": 20, "safety": 70, "comfort": 10},
}

SUBWEIGHTS = {
    "sidewalks": {"sidewalk":  1.00},
    "safety":    {"traffic":   0.65, "crash":    0.20, "hazards": 0.10, "flooding": 0.05},
    "comfort":   {"shade":     0.40, "exposure": 0.25, "slope":   0.35},
}
```

> **Note on `safety` sub-weights.** The original allocation was 40/35/15/10. The baked corridor has very sparse `crash_norm`, `hazard_norm`, and `flooding` (>99% of segments are 0). With those weights, dragging `safety` higher lowered mean risk less than dragging `sidewalks` higher, which made the slider feel broken. Rebalancing to 65/20/10/5 puts the responsive variable (`traffic_risk`) in the driver's seat. The other three remain non-zero so they re-engage as data coverage broadens.

**Per-segment risk** (`app/scoring.py:segment_risk`):

```python
risk = (
    w["sidewalk"] * (1 - sidewalk_cov)
  + w["traffic"]  * traffic_risk
  + w["crash"]    * crash_norm
  + w["hazards"]  * hazard_norm
  + w["shade"]    * (1 - canopy_pct)
  + w["exposure"] * exposure_norm
  + w["slope"]    * slope_risk
  + w["flooding"] * flooding
  + crossing_penalty
)
risk = clip(risk, 0.0, 1.0)
```

`crossing_penalty` is a flat, pre-computed per-segment value (`[0.0, 0.225]`, scaled by lanes/signalization) added *outside* the weighted sum; when `step_free=True` it's multiplied by **2.5**.

**Hard-avoids** (`step_free=True`): `risk = float("inf")` when a segment's `barrier` flag is set (steps OR `wheelchair=no` OR grade > 10%). The safe Dijkstra treats `inf` as `1e12`; mean-risk reporting excludes infinite-risk segments.

**Route score** = mean over segment risks (excluding hard-avoids). **Display score** = `round((1 − risk) × 100)` per segment — drives the gradient color ramp.

**Explanation builder** totals each factor's contribution along the route, picks the dominant one, and emits a one-line plain-language reason plus the sidewalk-coverage percentage.

### 6d. Data pipeline (`scripts/prebake.py`)

Run once to produce `outputs/scored_segments.parquet`; request-time work is then a cheap spatial snap and a graph cost-update.

1. **Walk-network build** (`network/build.py`):
   - Read cached Overpass JSON from `data/osm/<corridor>.json`.
   - Parse into ways + nodes; filter to walk-eligible `highway` classes.
   - `segmentize_edges` cuts ways into ~25 m pieces; tail pieces under 3 m get merged.
   - `node_segments` splits each piece at intersections so junctions become *shared endpoints* — without this the graph shatters into thousands of islands.
   - `explode_tags` flattens the OSM `tags` dict into typed columns.

2. **Factor modules** (each emits a clean `[0, 1]` Series indexed by `segment_id`; failures fall back to the documented null default and a warning is recorded in the sidecar):

   | Column | Module | Source | Method |
   |---|---|---|---|
   | `sidewalk_cov` | `layers/sidewalk.py` | OSM tags + ARC Clayton sidewalk geometry (6 m buffer union in EPSG:32616) | OSM=yes → 0.6 + 0.4·arc_frac; OSM=no → 0.5·arc_frac; unknown → arc_frac. Short segments (<8 m) fall back to OSM only. |
   | `traffic_risk` | `layers/traffic.py` | OSM `highway` class + `maxspeed` + GDOT AADT (`data/gdot/aadt_2017.geojson`, snapped to ways within 50 m, propagated by `osm_way_id`) | 0.40·class + 0.30·speed (step at 35/45 mph) + 0.30·AADT sigmoid (cap 0.80, midpoint 22.5k). |
   | `crash_norm` | `layers/crash.py` | GDOT statewide pedestrian crashes 2020–24 (Clayton County filter) | KABCO-weighted count within 30 m of each segment, min-max normalized. |
   | `hazard_norm` | `layers/hazards.py` | Atlanta 311 sidewalk reports **∪** live Supabase `gap_reports` | `max(type_weight × (1 − dist_m/20))` over hazards within 20 m. Max-not-sum — point penalty, not density. **ATL311 is empty for this corridor**; `gap_reports` is the operative source. |
   | `canopy_pct` | `layers/canopy.py` | Meta/WRI `cover5m` 10° COG tiles via S3 (keyless) | Sample nearest pixel at each segment's representative point; ÷1000 to `[0, 1]`. |
   | `exposure_norm` | `layers/exposure.py` | OpenMeteo ERA5 archive API (keyless) | Mean summer (Jun–Aug 2024) daily-max temperature at the corridor centroid, normalized to `[28°C → 0, 40°C → 1]`. ERA5's ~25 km grid is coarser than the corridor, so the base score is uniform; the orchestrator modulates by `(1 − canopy_pct)` so shaded segments score lower exposure. Fallback (API unreachable) = 0.65, not 0. |
   | `slope_risk` | `layers/slope.py` | USGS 3DEP 1/3 arc-sec DEM via S3 (keyless) | Endpoint elevations from the COG; grade = |Δz| / run. Linear ramp 5%→0, 8.33%→1. Grade > 10% sets `barrier=True`. |
   | `crossing_penalty` | `layers/crossing.py` | OSM `highway=crossing` + `traffic_signals` nodes | Buffer-intersect with segments → flat `base × width_factor × signalization_factor` ∈ `[0, 0.225]`. |
   | `flooding` | `layers/flooding.py` | FEMA NFHL REST API (`MapServer/28`, `SFHA_TF='T'`) | 1.0 if a segment intersects a Special Flood Hazard Area, else 0.0. |

3. **Post-processing:** `apply_exposure_shade` modulates uniform heat by canopy; `merge_barriers` ORs `crossing.barrier` and `slope.barrier` into a single `barrier` column; `coerce_numeric` converts OSM `lanes`/`maxspeed`/`width` strings to floats.

4. **CRS:** all buffers/distances run in **UTM 16N (EPSG:32616)**; storage/serve geometry stays in **WGS84 (4326)**.

5. **Null handling:** missing canopy → 0 (unknown shade ≠ shaded). Missing `maxspeed`/AADT → road-class default. No hazard within 20 m → `hazard_norm = 0` (silence ≠ danger). Exposure API down → 0.65. Missing DEM → slope_risk = 0 + `barrier=False`.

6. **Validation:** strict NaN assertion on every factor column. Sidecar JSON records per-column stats, OSM-cache SHA256, git HEAD, R4 module failures, and canary warnings for any column with only one distinct value (excluding `flooding` + `exposure_norm`, which are legitimately uniform).

7. **Atomic write:** tmpfile in the target's directory → `os.replace` into place.

**Current baked corridor stats** (from the committed sidecar): 30,723 segments; `sidewalk_cov` mean 0.111 / 1,713 distinct; `traffic_risk` mean 0.210 / 53 distinct; `slope_risk` mean 0.087; `canopy_pct` mean 0.148; `flooding` covers 1.5% of segments.

### 6e. Storage (Supabase / PostGIS)

`backend/supabase/schema.sql` (reconciled by `migrations/0001_…sql`; status workflow added in `migrations/0002_…sql`):

```sql
create table public.gap_reports (
    id          uuid primary key default gen_random_uuid(),
    geom        geography(Point, 4326) not null,
    type        text not null check (type in (
                    'broken_sidewalk','no_sidewalk','no_crossing',
                    'obstruction','streetlight_out','other'
                )),
    note        text,
    photo_url   text,
    reported_at timestamptz not null default now(),
    status      text not null default 'reported'
                  check (status in ('reported','in_progress','processed')),
    lng         double precision generated always as (st_x(geom::geometry)) stored,
    lat         double precision generated always as (st_y(geom::geometry)) stored
);
create index gap_reports_geom_idx on public.gap_reports using gist (geom);

-- RLS: anon can SELECT and INSERT (demo). Realtime publication is on.
-- Public Storage bucket `gap-photos` holds verified photos; the backend uploads
-- with the service-role key, so RLS doesn't gate the insert path in practice.
```

**Generated `lng` / `lat` columns** appear in realtime INSERT payloads, so a freshly inserted pin can be drawn without a follow-up read.

**Realtime flow:** the FastAPI backend (not the browser) inserts into `gap_reports`; the realtime publication pushes the row to every browser subscribed via `getSupabase().channel("gap_reports_live")`. Status changes propagate as UPDATE events.

**Hazards loop:** `layers/hazards.py` reads `gap_reports` directly from Supabase at bake time. Confirmed pins feed the next bake's `hazard_norm`.

### 6f. Factor catalog

| Factor | Role | Source | Status in current parquet |
|---|---|---|---|
| Sidewalk presence | `sidewalks` slider | OSM + ARC Clayton sidewalks | mean 0.11, 1.7k distinct values |
| Traffic danger | `safety` (×0.65) | OSM class + maxspeed + GDOT AADT 2017 | mean 0.21, sole responsive safety factor on the demo corridor |
| Crash history | `safety` (×0.20) | GDOT pedestrian crashes 2020–24 (Clayton filter) | sparse — mean 0.003 |
| Reported hazards | `safety` (×0.10) | ATL311 ∪ Supabase `gap_reports` | sparse — ATL311 empty in Clayton; `gap_reports` is the live driver |
| Crossings | flat per-segment penalty | OSM `crossing` / `traffic_signals` nodes + lanes | shipped |
| Shade | `comfort` (×0.40) | Meta/WRI canopy-height COG | mean 0.15 |
| Heat | `comfort` (×0.25) | OpenMeteo ERA5 summer mean max temp | uniform pre-modulation, varied post-canopy |
| Slope | `comfort` (×0.35) | USGS 3DEP DEM | mean 0.09, 3.5k distinct values |
| Hard-avoids | `step_free` constraint | OSM `steps`/`wheelchair=no` + grade > 10% | sets `risk = inf` for the safe Dijkstra |
| Flooding | `safety` (×0.05) | FEMA NFHL SFHA polygons | 1.5% of segments intersect |

**Rejected on principle** (see §8): crime, land use / CPTED, vacant / blight, noise.

## 7. Data sources

| Use | Source | Access |
|---|---|---|
| Sidewalks (ARC) | ARC Clayton sidewalks (`data/arc/clayton_sidewalks.geojson`) | GeoJSON, cached |
| Roads / crossings | OpenStreetMap via Overpass | JSON, cached to `data/osm/<corridor>.json` |
| Traffic volume | GDOT TADA 2008–2017 (`data/gdot/aadt_2017.geojson`) | GeoJSON |
| Pedestrian crashes | GDOT statewide 2020–24 (`backend/data/Crashes_2020-2024.geojson`) | GeoJSON, Clayton + KABCO filter |
| Reported hazards | Atlanta 311 GeoJSON + live Supabase `gap_reports` | GeoJSON + Postgres |
| Sidewalk inventory overlay (display) | City of Atlanta `Sidewalks_Inventory` ArcGIS | proxied via `/api/sidewalks` |
| Canopy | Meta/WRI `cover5m` v3 | 10° COG tiles on AWS, keyless |
| Heat | OpenMeteo ERA5 archive | JSON API, keyless |
| Slope | USGS 3DEP 1/3 arc-sec | 1° COG tiles on AWS, keyless |
| Flooding | FEMA NFHL REST | ArcGIS REST GeoJSON |
| Photo verification | Google Gemini (`gemini-2.5-flash` default) | `google-genai` SDK with structured output schema |
| Storage / realtime | Supabase Postgres + Storage + Realtime | managed |
| Base map | OpenFreeMap `liberty` style | keyless |
| Geocoding (autocomplete) | Mapbox (optional) → OSM Nominatim fallback | REST |
| Walking directions | Mapbox Directions for `POST /score` only | REST |
| Walking fallback | `router.project-osrm.org/route/v1/foot` | keyless REST |

## 8. Ethics & equity

**Guiding principle — *score the road, not the neighborhood.*** A factor is legitimate only if it measures a **physical hazard at a specific point** that endangers anyone walking there. Factors that measure a **social/area characteristic** are rejected — they penalize neighborhoods for their demographics and quietly reinstate redlining.

**Factors deliberately rejected:**
- **Crime data** — encodes policing bias; routing low-income Black riders away from their own neighborhoods replicates redlining ([ACLU on Microsoft's "avoid the ghetto" routing](https://www.aclu.org/news/national-security/your-turn-turn-navigation-application-racist)). The AJPH 2023 redlining/fatality link shows *infrastructure + crash* data captures the real danger without the bias.
- **Land use / "eyes on the street" (CPTED)** — industrial/vacant land concentrates in historically redlined areas *because of* disinvestment, so scoring it "less safe" reproduces that pattern.
- **Vacant / blighted property** — a discretionary, enforcement-driven label that maps almost exactly onto disinvested Black neighborhoods.

**Bias mitigations on the factors we *do* use:**
- **Reported hazards (311 + our reports):** scored as `max(type_weight × (1 − dist/20m))`, not as a density sum — a hazard anywhere counts; silence never implies safety. The Gemini gate is a junk-photo filter, not a content moderator; its job is to reject selfies, screenshots, and food photos so the map stays a sidewalk-hazard map. Confidence floor is `0.55`.
- **Accessibility:** the `step_free` toggle + `slope` factor + hard-avoids serve mobility-limited riders, who intersect heavily with the low-income transit-dependent population.

## 9. Key design decisions

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Routing | (a) Smart wrapper for `/score` + (b) **own Dijkstra over the scored graph** for `/route` | Build a routing engine from scratch | The Dijkstra returns paths Mapbox would never return (the deliberately-safer parallel that adds half a mile) |
| Frontend map | MapLibre GL JS + OpenFreeMap tiles | Mapbox GL JS | No Mapbox token needed for the map face |
| Geo backend | FastAPI + Shapely/GeoPandas | Turf.js (Node) | Need raster zonal stats + real CRS handling |
| Storage | Supabase (Postgres + PostGIS + Realtime + Storage) | MongoDB | Native PostGIS, generated `lng`/`lat` for realtime payloads, free photo bucket |
| Heat source | OpenMeteo ERA5 | NIHHIS-CAPA Atlanta raster | ERA5 is keyless, current, and modulated by canopy at segment scale |
| Shade signal | Meta `cover5m` (≥5 m canopy) COG | OSM trees / NDVI | OSM too sparse; NDVI conflates grass with trees |
| Slope DEM | USGS 3DEP (bare earth) | Copernicus GLO-30 (DSM) / OpenMeteo elevation | GLO-30 includes buildings (impossible grades); OpenMeteo is rate-limited per point |
| Photo gate | Gemini structured output | Manual moderation queue | Model can decide in <1 s with a clear rejection sentence |

## Appendix A — slider model (`backend/app/scoring.py`)

```python
FACTORS = ("sidewalk", "traffic", "crash", "hazards",
           "shade", "exposure", "slope", "flooding")

SLIDER_DEFAULTS = {
    "light": {"sidewalks": 30, "safety": 55, "comfort": 15},
    "dark":  {"sidewalks": 20, "safety": 70, "comfort": 10},
}

SUBWEIGHTS = {
    "sidewalks": {"sidewalk":  1.00},
    "safety":    {"traffic":   0.65, "crash":    0.20, "hazards": 0.10, "flooding": 0.05},
    "comfort":   {"shade":     0.40, "exposure": 0.25, "slope":   0.35},
}

def resolve_weights_from_sliders(sidewalks=None, safety=None, comfort=None, theme="light"):
    d = SLIDER_DEFAULTS[theme]
    raw = {
        "sidewalks": float(sidewalks if sidewalks is not None else d["sidewalks"]),
        "safety":    float(safety    if safety    is not None else d["safety"]),
        "comfort":   float(comfort   if comfort   is not None else d["comfort"]),
    }
    raw = {k: max(0.0, v) for k, v in raw.items()}
    norm = {k: v / (sum(raw.values()) or 1.0) for k, v in raw.items()}
    out = {f: 0.0 for f in FACTORS}
    for slider, sw in norm.items():
        for factor, sub in SUBWEIGHTS[slider].items():
            out[factor] += sw * sub
    return out

def segment_risk(seg, w, step_free=False, crossing_penalty_value=0.0):
    if step_free and (seg.get("barrier") or seg.get("highway") == "steps"
                      or seg.get("wheelchair") == "no"):
        return float("inf")
    risk = (
        w["sidewalk"] * (1 - seg["sidewalk_cov"])
      + w["traffic"]  *  seg["traffic_risk"]
      + w["crash"]    *  seg["crash_norm"]
      + w["hazards"]  *  seg["hazard_norm"]
      + w["shade"]    * (1 - seg["canopy_pct"])
      + w["exposure"] *  seg["exposure_norm"]
      + w["slope"]    *  (seg["slope_risk"] or 0.0)
      + w["flooding"] *  (seg["flooding"] or 0.0)
      + crossing_penalty_value
    )
    return max(0.0, min(1.0, risk))
```

## Appendix B — pipeline pseudocode (`backend/layers/`)

```python
# layers/hazards.py — UNION of ATL311 + Supabase gap_reports.
# Point penalty (max-not-sum) with distance decay.
HAZARD_W = {"broken_sidewalk":1.0, "obstruction":1.0, "no_sidewalk":0.9,
            "no_crossing":0.8,    "streetlight_out":0.4, "other":0.5}
near = gpd.sjoin_nearest(segs_utm, hazards_utm, max_distance=20, distance_col="d")
near["s"] = near["weight"] * (1 - near["d"] / 20).clip(lower=0)
hazard_norm = near.groupby(level=0)["s"].max().reindex(segments.index, fill_value=0.0)

# layers/canopy.py — Meta cover5m sampled at each segment's rep point.
clip = raster.rio.clip_box(*_CLAYTON_BBOX).squeeze(drop=True)
pts = segments.to_crs(4326).geometry.representative_point()
canopy_pct = np.clip(clip.sel(x=pts.x, y=pts.y, method="nearest").to_numpy() / 1000.0, 0, 1)

# layers/slope.py — endpoint elevations on USGS 3DEP, |Δz|/run in metres.
z0 = dem.sel(x=start_x, y=start_y, method="nearest").to_numpy()
z1 = dem.sel(x=end_x,   y=end_y,   method="nearest").to_numpy()
grade = np.where(run >= 1.0, np.abs(z1 - z0) / run, 0.0)
slope_risk = np.clip((grade - 0.05) / (0.0833 - 0.05), 0, 1)
barrier    = grade > 0.10

# layers/exposure.py — OpenMeteo ERA5 archive, summer 2024 mean daily-max.
# Uniform across corridor at ERA5 resolution; modulated by canopy post-hoc.
mean_temp_c = mean(httpx.get(_OPENMETEO_URL).json()["daily"]["temperature_2m_max"])
exposure_norm = clip((mean_temp_c - 28) / (40 - 28), 0, 1)
exposure_norm = ((1 - canopy_pct) * exposure_norm).clip(0, 1)  # in prebake.py, after canopy
```
