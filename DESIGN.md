# SidewalkSOS — Engineering Design Document

**Tagline:** Safe-walk routing + crowdsourced gap-mapping that makes the *last mile to MARTA* walkable for Atlanta's transit-dependent riders — and hands the city the data to fix what's broken.

| | |
|---|---|
| **Event** | Cox "Play With Purpose" Sustainability Hackathon — Track 04 *Moving Things & People* (Delta Air Lines + Mercedes-Benz), Problem #2 *Transit Equity & First/Last Mile* |
| **Location / Dates** | Atlanta, GA · June 14–17, 2026 |
| **Status** | Draft for build — decisions locked, open questions flagged in §17 |
| **Team** | R1 Frontend + Pitch · R2 Backend + Scoring · R3 Data: Network & Pipeline · R4 Data: Hazards, Env & Supabase |
| **Judging weights** | Value 20% · Innovation 25% · Polish 25% · Impact 30% |

---

## 1. Executive summary

MARTA spent 2026 fixing how riders move *between* stops (Reach on-demand microtransit, the NextGen bus redesign, Better Breeze tap-to-pay). The gap that remains is the **walk to the stop**: in low-income South Atlanta, the last mile is often a sidewalk-less arterial, an unsignalized crossing, unshaded pavement, or a crash hotspot — which silently severs transit-dependent residents from transit they live next to. SidewalkSOS is a web app that routes riders on the **safest** walk between a MARTA stop and their destination (not the shortest), explains why, and — where no safe route exists — lets them report the gap into a live map that becomes a Vision Zero deliverable for the city. It is built as a thin "smart wrapper": off-the-shelf map + routing, with our only original code being a multi-factor safety-scoring layer that ranks routes by a metric the map APIs don't expose.

## 2. Problem statement & motivation

The first/last-mile problem is usually framed as **distance** (the stop isn't at your door). MARTA already built the vehicle answer in 2026. The unsolved problem is **walkability**: whether the last-mile distance is *traversable on foot at all*.

- **MARTA's 2026 baseline (so we don't rebuild what shipped):** Reach went permanent Mar 7, 2026 (12 zones, $2.50 fare) ([ATL News 2026](https://georgia.atl.news/2026/03/04/from-pilot-to-permanent-marta-reach-officially-launches-system-wide-this-saturday/)); the NextGen bus network launched Apr 18, 2026 ([Metro Magazine](https://www.metro-magazine.com/news/marta-set-to-launch-next-gen-bus-network)); Better Breeze contactless launched Mar 28, 2026 ([Urbanize Atlanta 2026](https://atlanta.urbanize.city/post/marta-better-breeze-upgraded-tap-pay-system-goes-live-soon)).
- **The equity geography:** MARTA serves Fulton + DeKalb (+ Clayton since 2014); Cobb (1965) and Gwinnett (1971/1990/2019/2020) repeatedly opted out ([AJC 2025](https://www.ajc.com/news/2025/10/riding-the-color-line-how-race-built-atlantas-marta-system/)). The I-20 line was a *designed* racial boundary (1960 plan) ([Smart Growth America 2023](https://www.smartgrowthamerica.org/signature-reports/divided-by-design/atlantas-story/)).
- **The danger is measurable and unequal:** historically redlined tracts have **2.6× higher pedestrian-fatality rates** ([Taylor et al., AJPH 2023](https://ajph.aphapublications.org/doi/10.2105/AJPH.2022.307192)).
- **The canonical failure case:** at Gillem Logistics Center, transit workers walk **1.5–2 miles along busy roadways with no sidewalk**; many transit commutes exceed an hour ([Georgia Tech / ARC 2022](https://atlantaregional.org/whats-next-atl/articles/marta-reach-aims-to-address-first-mile-last-mile-issue/)).
- **The stakes:** Atlanta is ~81% drive / ~2% transit, transit commute ~53 min ([ACS via Streetsblog 2019](https://usa.streetsblog.org/2019/05/06/study-commutes-are-longer-when-cities-fail-on-transit)); only ~30% of metro jobs are reachable in 90 min by transit ([Brookings 2011](https://www.brookings.edu/articles/missed-opportunity-transit-and-jobs-in-metropolitan-america/)).

## 3. Goals & non-goals

**Goals**
- Route a rider on the *safest* walk (multi-factor), not the shortest, between a MARTA stop and a destination.
- Explain *why* a route is safer, in plain language.
- Capture rider-reported gaps into a live map usable by the city.
- Tell a credible, sourced equity + sustainability story for judges.

**Non-goals (explicit)**
- **We do NOT extend MARTA's network reach.** We do not get rail to Cumberland/Gwinnett. We operate within walking range of existing stops.
- We do not build microtransit, fare payment, or trip-planning across modes (MARTA already shipped these).
- We do not produce certified ADA routing — `accessible` mode is a best-effort signal (§7c), not a compliance guarantee.
- **Not citywide for v1 — one corridor** (a *deliberate* MVP scope, not a code limitation). `prebake.py` is **parameterized by a bounding box**, so citywide is a config change, not a rewrite — we bake and *ground-truth* one corridor to keep every safety claim defensible (see §11).

## 4. Target users & key use cases

**Primary persona — "Marcus," carless shift worker.** Commutes by bus to a logistics job; the bus drops him ~1 mile out along a road with no sidewalk. No car, prepaid phone, limited data. Needs: the safest way to walk the last mile; reassurance before he sets out in the dark.

**Secondary persona — "Dana," city/ARC planner.** Runs Vision Zero analysis. Needs: evidence of where the pedestrian network fails transit riders, to prioritize sidewalk capital.

**Use cases**
1. Stop → job: see safe vs. default route, pick the safe one.
2. Tune priorities (slider / `night` / `accessible`) and watch the route adapt.
3. Encounter a missing sidewalk → report it → it appears live for others and on the planner dashboard.

## 5. Solution overview

**Reframe:** the last mile is a *walkability* problem; walking is the only free, universal, no-app/no-bank first/last-mile mode, so making the walk safe restores access for the people most excluded.

**Positioning line:** *"MARTA fixed the bus. We fix the part they can't — the walk to it. And where there's no safe walk, we hand the city the map."*

**User flow:** pick stop + destination → backend fetches candidate walking routes → scores each against pre-baked safety data → returns the safest (green) + the default (red), colored per-segment → rider tunes weights/profile → optionally reports a gap → gap appears live on the map and the dashboard.

## 6. System architecture

```
 Browser — Next.js + Mapbox GL JS                         (Vercel)
   │  geocode address · "draw this route" · report a gap
   ▼
 FastAPI + Shapely/GeoPandas                       (Render/Railway, Docker)
   │  1) Mapbox Directions  → default + alternative walking routes   ← WRAPPER
   │  2) snap routes to pre-scored segments, apply weights/profile    ← OUR VALUE
   │  3) return safest + per-segment risk as GeoJSON
   ▼
 scored_segments.parquet   +   Supabase / PostGIS (gap_reports, realtime)
   ▲ built offline by prebake.py
```

**Component responsibilities**
- **Frontend + Pitch (R1):** all UI — map, route layers, sliders/profile toggle, comparison panel, gap-report UI, realtime dashboard; Vercel deploy; deck/demo/offline-cache/QA.
- **Backend + Scoring (R2):** `/score`, the Mapbox Directions wrapper, route→segment snap, weighted scoring, profiles + hard-avoids, explanation strings; container deploy.
- **Data — Network & Pipeline (R3):** corridor + bbox, OSM network build, the `prebake.py` orchestrator, and the `sidewalk`/`traffic`/`crossing`/`slope` factor modules → `scored_segments.parquet`.
- **Data — Hazards, Environment & Supabase (R4):** the `crash`/`hazards`/`canopy`/`exposure`/`flooding` factor modules; Supabase `gap_reports` schema + seed + realtime.

**The Smart-Wrapper principle:** the base map, geocoding, and routing are off-the-shelf API calls. Our entire original contribution is step 2 — ranking the routes Mapbox already returns by a safety metric it doesn't have. Don't build a routing engine.

**Deployment topology:** frontend on Vercel; FastAPI in a `python:3.12-slim` Docker container on Render/Railway/Fly (GeoPandas/rasterio's compiled deps are painful in serverless); Supabase managed Postgres.

## 7. Detailed design

### 7a. Frontend
- **Stack:** Next.js + TypeScript, **Mapbox GL JS** (map face), **Mapbox Directions + Geocoding** (wrapper APIs), Tailwind + shadcn/ui. Zero-billing fallback: MapLibre GL JS.
- **Components:**
  - `MapView` — base map on corridor, stop + destination markers, route layers (safe/default), per-segment risk coloring via data-driven line styling.
  - `RoutePanel` — default vs. safe comparison: distance, minutes, % no-sidewalk, % shaded, plain-language reason.
  - `WeightControls` — the factor sliders + `day`/`night`/`accessible` profile toggle → re-calls `/score`, re-renders. **The hero interaction.**
  - `GapReport` — "Report a gap" → tap location → `INSERT` into Supabase *(R1 UI, writing to R4's `gap_reports` table)*.
  - `Dashboard` — realtime gap pins + heatmap; the "city deliverable" view.
- **Client module** `api/` — typed calls to `/score` and Supabase; runs against a **mock `/score` JSON** until the backend is live.
- **Polish:** mobile-responsive, loading/empty/error states (Polish = 25%).

### 7b. Backend API

`POST /score`

```jsonc
// request
{
  "origin": [-84.40, 33.69],            // [lon, lat] — a MARTA stop
  "dest":   [-84.35, 33.71],            // [lon, lat] — destination
  "weights": { "sidewalk":0.5, "traffic":0.2, "crash":0.2, "hazards":0.1 },  // optional
  "profile": "night"                    // optional; "day"|"night"|"accessible"; ignored if weights present
}
// response
{
  "safest": { "score": 0.18, "minutes": 14.2, "geojson": { /* FeatureCollection, per-segment risk */ } },
  "alternatives": [ { "score": 0.41, "minutes": 12.0, "geojson": {…} }, … ]
}
```

`GET /health` → `{"status":"ok"}`. CORS enabled for the Vercel origin.

**Concurrency:** handlers that call Shapely/GeoPandas are declared `def` (not `async def`) so FastAPI runs them in a threadpool and CPU-bound geo work never blocks the event loop.

### 7c. Scoring model

Per-segment weighted sum of normalized (0–1) factors; route score = mean of segment risk; lowest score wins.

| Weight key | Parquet column | Meaning (1 = …) | Risk term |
|---|---|---|---|
| `sidewalk` | `sidewalk_cov` | sidewalk present | `w·(1 − sidewalk_cov)` |
| `traffic` | `traffic_risk` | dangerous road *(class + speed + AADT volume)* | `w·traffic_risk` |
| `crash` | `crash_norm` | crash hotspot / on HIN | `w·crash_norm` |
| `hazards` | `hazard_norm` | reported physical hazard present | `w·hazard_norm` |
| `shade` | `canopy_pct` | fully shaded | `w·(1 − canopy_pct)` |
| `exposure` | `exposure_norm` | high heat / pollution exposure | `w·exposure_norm` |
| `slope` *(Tier 2)* | `slope_risk` | steep grade | `w·slope_risk` |

- **Two data-enrichment signals fold into existing columns, not new sliders:** vehicle **volume (GDOT AADT)** enriches `traffic_risk`; crossing **signalization + lane/width** enriches the crossing penalty (below).
- **`hazards`** = the *union* of [SeeClickFix Open311](https://seeclickfix.com/open311/v2/requests.json?lat=33.749&long=-84.388) physical-hazard reports (broken sidewalk, streetlight-out, obstruction) **and** our own crowdsourced `gap_reports` — scored as **point penalties, never as complaint density** (see §9).
- **`exposure`** = heat (NIHHIS-CAPA Atlanta raster) + optional pollution (EJScreen traffic-proximity); doubles as the sustainability/Impact slider in the demo.
- **Weight resolution:** weights are relative; the backend clamps negatives, ignores unknown keys, and **normalizes to sum 1** (falls back to defaults if all zero). See Appendix A.
- **Profiles** (7 keys, see Appendix A): `day` (balanced), `night` (boost crash + traffic + hazards, drop shade/exposure), `accessible` (boost sidewalk + slope; hard-avoids on). `profile` selects a preset; explicit `weights` win.
- **Crossings:** a **fixed per-node penalty** at crossings, scaled by **road width/lanes and lack of signalization** (wide unsignalized crossings are a top death cause) and ×~2.5 in `accessible`; not a user weight.
- **Hard-avoids (accessible only):** segments with `highway=steps`, `wheelchair=no`, or grade > 10% get `risk = inf` so route selection skips any path containing them — accessibility barriers are *constraints*, not preferences.

### 7d. Data pipeline (`prebake.py`, offline)

Run once to produce `scored_segments.parquet`; request-time work is then a cheap spatial snap, not heavy geo computation.

1. Build the walk network for the corridor from OSM (Overpass), `segmentize` to ~25 m pieces.
2. Attach normalized 0–1 columns:
   - `sidewalk_cov` ← OSM `sidewalk`/`footway` + ARC sidewalk layer.
   - `traffic_risk` ← OSM `highway` class (+ `maxspeed` where present, else class default) **+ GDOT AADT volume** snapped from count stations.
   - `crash_norm` ← ATLDOT HIN / GDOT crash density, min-max scaled (FARS as severity overlay).
   - `hazard_norm` ← **SeeClickFix Open311 physical-hazard points ∪ `gap_reports`**, flagged within ~20 m of the segment (union, not density).
   - `canopy_pct` ← Meta/WRI 1 m canopy-height zonal stats, threshold ≥3 m (optional NAIP NDVI > 0.3 refine).
   - `exposure_norm` ← **NIHHIS-CAPA Atlanta heat raster** zonal mean (+ optional **EJScreen** block-group traffic-proximity), normalized.
   - `slope_risk` ← DEM grade, normalized 5%→0, 8.33%→1; grade > 10% → barrier flag.
   - `crossing` / `barrier` flags ← OSM `crossing` + `traffic_signals` + `lanes`/`width`, `kerb`, `wheelchair`, `steps`.
3. **CRS:** reproject to **UTM 16N (EPSG:32616, metres)** for all buffers/distances; store/serve geometry in **WGS84 (4326)**.
4. **Null handling:** missing `canopy_pct` → 0 (or corridor mean); missing `maxspeed`/AADT → road-class default; **no reported hazard → `hazard_norm` = 0** (silence ≠ danger, and ≠ safety); missing `exposure` → corridor mean; missing `kerb`/`wheelchair` → **unknown, not "no ramp"** (don't over-block). Document the method (judges + §8 ask).
5. Write GeoParquet; build `gdf.sindex` at backend startup.

### 7e. Storage (Supabase / PostGIS)

```sql
create table gap_reports (
  id          bigint generated always as identity primary key,
  geom        geometry(Point, 4326) not null,
  type        text not null,                  -- 'no_sidewalk' | 'no_crossing' | 'obstruction' | …
  note        text,
  photo_url   text,
  created_at  timestamptz not null default now()
);
create index gap_reports_geom_gix on gap_reports using gist (geom);
-- RLS: allow anon INSERT + SELECT (demo); enable realtime publication on the table.
```

**Realtime flow:** frontend `INSERT`s a report through the auto-generated Supabase API and subscribes to the table's realtime channel → new pins appear live on every open map (a strong crowdsourcing demo moment). Backend can `read_postgis()` reports straight into a GeoDataFrame to feed the `hazards` factor.

### 7f. Factor catalog

Every safety signal in one place. **Status:** Core = ship for sure · Stretch = if time · Supplementary = nice-to-have · Rejected = excluded on principle (see §9).

| Factor | Role | Data source | Granularity | Status | Bias risk |
|---|---|---|---|---|---|
| Sidewalk presence | `sidewalk` weight | OSM `sidewalk`/`footway` + ARC | per-segment | **Core** | Low |
| Traffic danger | `traffic` weight | OSM `highway`/`maxspeed` **+ GDOT AADT** | per-segment (AADT arterial-only) | **Core** | Low |
| Crash history | `crash` weight | ATLDOT HIN / GDOT / NHTSA FARS | point → segment | **Core** | Low |
| Reported hazards | `hazards` weight | SeeClickFix Open311 ∪ `gap_reports` | point → segment | **Core** | Low–Med\* |
| Crossings | fixed node penalty | OSM `crossing`/`traffic_signals`/`lanes` | node | **Core** | Low |
| Shade | `shade` weight | Meta/WRI 1 m canopy height (NAIP refine) | 1 m raster → segment | **Stretch** | Low |
| Heat / pollution | `exposure` weight | NIHHIS-CAPA heat (+ EJScreen pollution) | raster / block-group | **Stretch** | Low–Med\* |
| Slope | `slope` weight (Tier 2) | USGS 3DEP / Mapbox Terrain DEM | DEM → segment | **Stretch** (accessible) | Low |
| Hard-avoids | constraint (accessible) | OSM `steps`/`wheelchair` + DEM grade > 10% | segment / node | **Accessible mode** | Low |
| Flooding | not yet wired | FEMA NFHL + ARC Floodplains | polygon | **Stretch** | Low |
| Lighting | not yet wired | VIIRS / OSM `street_lamp` | ~500 m / point | **Supplementary** | Med\* |
| Crime | — | APD open data | point | **Rejected** | High |
| Land use / CPTED | — | parcels / zoning / OSM | parcel | **Rejected** | High |
| Vacant / blight | — | Accela / county assessor | address | **Rejected** | High |
| Noise | — | BTS National Noise Map | 30 m raster | **Rejected** (redundant w/ traffic) | Low |

\* Bias-mitigated per §9 (hazards scored as points-not-density; lighting/pollution normalized within-neighborhood).

## 8. Data sources & licensing

| Factor / use | Source | Format / access | Notes & flags |
|---|---|---|---|
| Stops / network | [MARTA GTFS + GTFS-RT](https://itsmarta.com/app-developer-resources.aspx) | zip + protobuf, **keyless** | Confirmed public |
| Sidewalks/roads/crossings/barriers | [OSM via Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) + [ARC hub](https://opendata.atlantaregional.com/) | GeoJSON | `highway` complete; `maxspeed`~40%, `kerb`/`wheelchair` **sparse** — cache once |
| Crashes | [ATLDOT Vision Zero HIN](https://atldot.atlantaga.gov/programs/vision-zero); GDOT Numetric; [NHTSA FARS API](https://crashviewer.nhtsa.dot.gov/CrashAPI) | dashboard / CSV / API | **HIN downloadable geometry UNVERIFIED** — dig ArcGIS REST; FARS = fatalities only |
| Shade | [Meta/WRI 1 m Canopy Height](https://registry.opendata.aws/dataforgood-fb-forests/) ([GEE](https://gee-community-catalog.org/projects/meta_trees/)) | COG / GEE, CC BY 4.0 | Height (not NDVI); ~2.8 m MAE, static 2018–2020. **GT Atlanta canopy is NOT public** |
| Shade refine *(opt)* | NAIP 0.6 m (USDA, `USDA/NAIP/DOQQ`) | GEE / ImageServer | 4-band NIR → NDVI |
| Slope *(Tier 2)* | USGS 3DEP / Mapbox Terrain-RGB | DEM raster | Gap-free; the dependable a11y signal |
| Traffic volume (→ `traffic_risk`) | [GDOT TADA](https://www.dot.ga.gov/GDOT/Pages/RoadTrafficData.aspx) / [ARC mirror](https://opendata.atlantaregional.com/datasets/c9ce7fe9c5f94f338422e4d5c7119158_0/about) | shapefile / GeoJSON | Point-station → snap to segments; clean mirror is 2008–2017, arterials only |
| Reported hazards (`hazards`) | [SeeClickFix Open311](https://seeclickfix.com/open311/v2/requests.json?lat=33.749&long=-84.388) ✅ live | JSON | Union with our `gap_reports`; participation bias → use as points, not density |
| Heat (`exposure`) | [NIHHIS-CAPA Atlanta](https://noaa.hub.arcgis.com/maps/b6c4c54a585d4a1ebe23d4599eec7cc2) | GeoTIFF / CSV, **keyless** | Street-relevant; single-day snapshot — **verify campaign year** |
| Pollution *(opt, `exposure`)* | [EJScreen v2.3 — Harvard Dataverse mirror](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/RLR5AX) | CSV / GDB | Block-group coarse; **EPA removed it Feb 2025 — cite the mirror, not epa.gov** |
| Flooding *(stretch)* | [FEMA NFHL REST](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer) + [ARC Floodplains](https://opendata.atlantaregional.com/datasets/b415a26bc2254341924d9f43f8056f16) | REST / polygon | Intersect segments; NFHL misses urban flash flooding |
| Lighting *(supplementary)* | VIIRS nighttime / OSM `street_lamp` | raster / GeoJSON | **No real Atlanta inventory exists**; ~500 m proxy — normalize within-neighborhood |
| Context | Census ACS B08201/B08301, LODES | API / CSV | For the "who's affected" framing |

## 9. Ethics & equity

**Guiding principle — *score the road, not the neighborhood.*** A factor is legitimate only if it measures a **physical hazard at a specific point** that endangers anyone walking there. Factors that measure a **social/area characteristic** are rejected — they penalize neighborhoods for their demographics and quietly reinstate redlining.

**Factors we deliberately rejected, and why** (this is a pitch slide, not a silent omission):
- **Crime data** — encodes policing bias (over-policed areas show more "crime"); routing low-income Black riders away from their own neighborhoods replicates redlining ([ACLU on Microsoft's "avoid the ghetto" routing](https://www.aclu.org/news/national-security/your-turn-turn-navigation-application-racist)). The [AJPH 2023](https://ajph.aphapublications.org/doi/10.2105/AJPH.2022.307192) redlining/fatality link shows *infrastructure + crash* data captures the real danger without the bias.
- **Land use / "eyes on the street" (CPTED)** — industrial/vacant land concentrates in historically redlined areas *because of* disinvestment, so scoring it "less safe" reproduces that pattern. Data exists; we don't use it.
- **Vacant / blighted property** — a discretionary, enforcement-driven label that maps almost exactly onto disinvested Black neighborhoods. Highest bias risk; excluded entirely.

**Bias mitigations on the factors we *do* use:**
- **Reported hazards (311 + our reports):** wealthier areas report more, so we score **specific hazard points as penalties, never complaint density** — a hazard anywhere counts; silence never implies safety.
- **Lighting / pollution:** normalized **within-neighborhood** (not citywide) so brighter/cleaner affluent corridors don't pull routes toward wealthy areas.

**Other equity considerations:**
- **Digital divide:** the app needs a smartphone — the same barrier we critique in MARTA's app. Acknowledged; SMS/low-bandwidth fallback is roadmap (§16).
- **Accessibility:** `accessible` profile + slope + hard-avoids serve mobility-limited riders, who intersect heavily with the low-income transit-dependent population.

## 10. Alternatives considered

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Routing | Smart Wrapper (score Mapbox alternatives) | Build a routing engine | Wrapper is reliable + demoable in 48h; engine is the time-sink trap |
| Geo backend | FastAPI + Shapely/GeoPandas | Turf.js (Node) | Need raster zonal stats (canopy/heat) + real CRS handling; consolidates language |
| Storage | Supabase/PostGIS | MongoDB | Native PostGIS + `read_postgis()` matches the geo stack; auto realtime API; instant spatial queries |
| Shade signal | Meta 1 m canopy **height** | OSM trees / NDVI | OSM too sparse; NDVI conflates grass with trees; height isolates real shade |
| Corridor | Gillem Logistics | Cumberland/Gwinnett | Those are *reach* gaps (no stop) we can't solve; Gillem is a *walk* gap we can |

## 11. Demo plan

- **Hero corridor:** Gillem Logistics Center — documented, a MARTA Reach pilot zone, an equity-rich job center, and unambiguously a walkability (not reach) failure.
- **Scope framing (bbox-parameterized):** `prebake.py` takes a bounding box, so the corridor is config, not hardcode. We bake + ground-truth one corridor for a defensible demo, and pitch the scope as a *choice*: *"We scoped v1 to one corridor so every safety claim is verified. The pipeline is corridor-agnostic — citywide is a bounding-box away. We chose a true demo over a broad, unverifiable one."*
- **Caveat / backup:** Gillem may lack a *safer parallel path* for the reroute moment (the documented problem is no sidewalk at all). Validate in hour 1; if absent, use Gillem for the **"no safe route → report gap"** beat and a **West Atlanta / Belvedere Park** segment (other Reach zones, denser, more route choice) for the **reroute** beat.
- **Script:** dangerous default (red) → safe alternative (green) + plain-language reason → drag a slider / switch to `accessible` → route changes live → tap "Report a gap" → pin appears live on the map + dashboard.
- **Safety net:** pre-cache the corridor's API responses + bundle the GeoJSON so the demo runs **offline**; record a 60-sec backup video.

## 12. Team, roles & contracts

No dedicated integration seat — integration is distributed (each owner deploys their own service; contracts are locked at kickoff). The heaviest role, **Data, is split cleanly into base-network vs. overlay-layers** so all four work in parallel on separate files.

| Role | Owns | Key deliverable |
|---|---|---|
| **R1** Frontend + Pitch | **All** UI (map, routes, sliders, comparison, gap-report, dashboard) + Vercel deploy + deck/demo/offline-cache/QA | Deployed demo URL + rehearsed pitch |
| **R2** Backend + Scoring | FastAPI, `/score`, Directions wrapper, `scoring.py` (snap + weighted sum + profiles + hard-avoids + explanations), container deploy | Live `/score` matching the contract |
| **R3** Data — Network & Pipeline | Corridor + bbox, OSM network build, `prebake.py` orchestrator, the `sidewalk`/`traffic`/`crossing`/`slope` factor modules | `scored_segments.parquet` (network + those columns) |
| **R4** Data — Hazards, Env & Supabase | The `crash`/`hazards`/`canopy`/`exposure`/`flooding` factor modules; Supabase `gap_reports` schema + seed + realtime | Overlay columns + live Supabase |

**The clean data split (how R3 & R4 avoid merge conflicts):** each factor is a separate module `layers/<factor>.py` exposing `score(segments) -> Series` (0–1, indexed by `segment_id`). R3 owns the base network + orchestrator + its modules; R4 owns its modules + Supabase. They touch **different files**; `prebake.py` just imports and column-joins on `segment_id`. R4 codes against a small **sample network** R3 publishes in hour 1, so neither blocks the other.

**Lock at kickoff (contracts + corridor):**
1. `/score` JSON shape *(R2 ↔ R1)*
2. `scored_segments.parquet` schema + `segment_id` index *(R3 ↔ R2)*
3. factor-module interface `score(segments) -> Series[0–1]` keyed by `segment_id` *(R3 ↔ R4)*
4. `gap_reports` schema + Supabase client API *(R4 ↔ R1)*
5. the hero corridor / bbox *(all)*

**Dependency chain:** `R3 base network → {R4 overlay columns, R2 scoring} → R1 render`. R3 publishes a sample network, R2 ships a **stub parquet**, and R1 builds on **mocks**, so all four start in parallel. Each owner deploys their own service. **R2 is the most bounded role — it floats to help R1 with the dashboard + QA on Day 2.**

## 13. Timeline & milestones

- **Kickoff (1–2h):** scaffold the apps, deploy hello-worlds, **lock contracts + corridor**; R3 publishes the sample network.
- **Day 1 AM:** R3 de-risks the network (Overpass pull, confirm sidewalk coverage, validate corridor route-choice) + `sidewalk`/`traffic`; R4 stands up Supabase + first overlay modules (`crash`/`hazards`); R2 `/score` on stub parquet; R1 map + mocked route.
- **Day 1 PM → 🟢 MILESTONE:** **real route, scored + colored, live on the corridor** (the thin slice).
- **Day 2 AM:** R1 polish + comparison + gap-UI/dashboard; R2 night/accessible profiles + edge cases (then floats to R1); R3 slope + crossing depth + AADT; R4 canopy/heat/(pollution) + seed pins.
- **Day 2 midday:** 🔒 **feature freeze** → all-hands bug-bash + pre-cache demo offline.
- **Day 2 PM:** rehearse 2× · submit.

## 14. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| OSM/ARC sidewalk coverage thin at corridor | Med | Validate hour 1; hand-trace the one corridor if needed |
| Gillem has no safer alternative to reroute | Med | Backup route-choice corridor (West Atlanta/Belvedere) |
| HIN crash geometry not downloadable | Med | Fall back to GDOT CSV + FARS + road-class proxy |
| Raster (canopy/heat/slope) skill gap | Med | Hand-tag corridor or use layers as visual overlay only |
| Too many factors → black-box / slider sprawl | Med | Cap user sliders ~5–6; fold AADT + crossings into existing columns |
| Scope creep (4 engineers over-build) | High | Cap at MVP; pour surplus into rehearsal + polish |
| Demo / wifi failure | Med | Offline pre-cache + backup video |

## 15. Success metrics & judging alignment

| Criterion (weight) | How we win it |
|---|---|
| **Value (20%)** | Sourced problem (2.6× stat, Gillem), the right-corridor choice, a real rider scenario |
| **Innovation (25%)** | Multi-factor safety scoring on open data; the deliberate crime-data exclusion **and** principled rejection of bias-prone factors (land use, blight) — "score the road, not the neighborhood" |
| **Polish (25%)** | Live slider-reroute demo, the comparison panel, shadcn UI, offline-proofed run |
| **Impact (30%)** | Car-trips-avoided (EPA: transport = largest US GHG), equity framing, the gap map as a Vision Zero/ARC handoff (answers "who sustains it"); the heat + pollution `exposure` factor doubles as a climate-resilience story |

## 16. Future work / roadmap

- **SMS / low-bandwidth fallback** (the "TextTransit" idea) for no-smartphone riders.
- Multi-corridor → citywide.
- Promote stretch factors (flooding, lighting) to first-class once data improves (e.g., a public ATLDOT [Light Up the Night](https://atldot.atlantaga.gov/programs/light-up-the-night) streetlight inventory).
- Full **walk → bus → walk** multimodal chain (OTP), with MARTA Reach pickup points as endpoints.
- Fold rider-reported gaps back into the scoring weights over time.

## 17. Open questions

1. Is the ATLDOT High-Injury Network downloadable as routable geometry, or dashboard-only?
2. Does the Gillem corridor have a safer parallel path (decides single- vs. dual-corridor demo)?
3. Which factors are core vs. stretch? (sidewalk/traffic/crash/hazards = core; shade, exposure, slope, flooding, lighting = optional)
4. Does Atlanta publish a municipal street-tree inventory (would sidestep the raster work)?
5. Photo uploads on gap reports — in or out for v1?
6. Is the EJScreen mirror vintage (~2017) + Harvard Dataverse citation acceptable for the pollution layer?
7. What is the NIHHIS-CAPA Atlanta heat campaign year, and does it cover the demo corridor?
8. Current-year GDOT AADT — clean source + the point-station → segment snapping method?
9. Expose `hazards` and `exposure` as user sliders, or keep them as fixed modifiers to limit slider sprawl?

## Appendix A — weight resolution & scoring (Python)

```python
FACTORS  = ("sidewalk", "traffic", "crash", "hazards", "shade", "exposure", "slope")
DEFAULTS = {"sidewalk":0.30, "traffic":0.20, "crash":0.20, "hazards":0.15,
            "shade":0.10, "exposure":0.05, "slope":0.00}
PROFILES = {
    "day":        DEFAULTS,
    "night":      {"sidewalk":0.20,"traffic":0.25,"crash":0.30,"hazards":0.15,
                   "shade":0.05,"exposure":0.05,"slope":0.00},   # boost crash+traffic+hazards
    "accessible": {"sidewalk":0.35,"traffic":0.10,"crash":0.10,"hazards":0.15,
                   "shade":0.05,"exposure":0.05,"slope":0.20},   # boost sidewalk+slope
}

def resolve_weights(weights: dict | None, profile: str | None) -> dict:
    raw = weights or PROFILES.get(profile or "day", DEFAULTS)
    raw = {k: max(0.0, float(raw.get(k, 0))) for k in FACTORS}   # clamp, ignore unknowns
    s = sum(raw.values()) or 1.0
    return {k: v / s for k, v in raw.items()}                    # normalize to sum 1

def segment_risk(seg, w, profile):
    if profile == "accessible" and (seg["barrier"] or seg["slope_risk"] is None):
        return float("inf")                                      # hard-avoid
    return ( w["sidewalk"] * (1 - seg["sidewalk_cov"])
           + w["traffic"]  *  seg["traffic_risk"]                # incl. AADT volume
           + w["crash"]    *  seg["crash_norm"]
           + w["hazards"]  *  seg["hazard_norm"]                 # 311 ∪ crowdsourced reports
           + w["shade"]    * (1 - seg["canopy_pct"])
           + w["exposure"] *  seg["exposure_norm"]               # heat (+ pollution)
           + w["slope"]    *  (seg["slope_risk"] or 0.0) )       # + crossing penalty at nodes
```

## Appendix B — prebake: canopy, slope, hazards & exposure (Python)

```python
import rasterio, pandas as pd, geopandas as gpd
from rasterstats import zonal_stats

buf = segments.to_crs(32616).buffer(5)   # 5 m sidewalk + overhang buffer per segment

# Shade: % of buffer under ≥3 m canopy (Meta/WRI height tile)
z = zonal_stats(buf, "data/meta_canopy.tif", stats=["mean"])
segments["canopy_pct"] = [min(max((s["mean"] or 0) / 6.0, 0), 1) for s in z]  # ~6 m → full

# Slope: grade from a DEM, normalized 5%→0, 8.33%→1; >10% = barrier
with rasterio.open("data/dem.tif") as dem:
    for i, seg in segments.iterrows():
        z0, z1 = [v[0] for v in dem.sample([seg.start, seg.end])]
        grade = abs(z1 - z0) / seg.length_m
        segments.at[i, "slope_risk"] = min(max((grade - 0.05) / (0.0833 - 0.05), 0), 1)
        segments.at[i, "barrier"]    = grade > 0.10

# Hazards: UNION of SeeClickFix 311 physical-hazard points + our gap_reports (NOT density)
haz = gpd.GeoDataFrame(pd.concat([open311_hazards, gap_reports]), crs=4326).to_crs(32616)
hit = gpd.sjoin_nearest(gpd.GeoDataFrame(geometry=buf), haz, max_distance=20).index.unique()
segments["hazard_norm"] = segments.index.isin(hit).astype(float)     # 1 if any hazard within 20 m

# Exposure: heat (CAPA raster) + optional pollution (EJScreen block-group), 0..1
zt   = zonal_stats(buf, "data/capa_heat.tif", stats=["mean"])
heat = normalize([s["mean"] or 0 for s in zt])                       # relative 0..1
poll = gpd.sjoin(segments, ejscreen_bg[["traffic_prox", "geometry"]], how="left")["traffic_prox"]
segments["exposure_norm"] = 0.7 * heat + 0.3 * normalize(poll.fillna(poll.mean()))
```

## Appendix C — consolidated sources
MARTA Reach (ATL News 2026) · NextGen (Metro Magazine) · Better Breeze (Urbanize 2026) · AJC 2025 (MARTA/race) · Brookings 2011 · AJPH 2023 (redlining/fatalities) · GT/ARC 2022 + arXiv 2308.02681 (Gillem/Reach) · Streetsblog 2019 (mode share) · Smart Growth America 2023 (I-20) · ACLU ("avoid the ghetto") · EPA transportation GHG · Meta/WRI canopy (AWS/GEE) · MARTA GTFS · ATLDOT Vision Zero / GDOT / NHTSA FARS · USGS 3DEP · GDOT TADA (AADT) · SeeClickFix Open311 (hazards) · NIHHIS-CAPA Atlanta (heat) · EJScreen v2.3 Harvard Dataverse (pollution) · FEMA NFHL + ARC Floodplains (flooding).
