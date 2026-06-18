# safewalk

Safe-walk routing + crowdsourced gap-mapping for MARTA first/last mile. See [DESIGN.md](DESIGN.md) for the engineering spec.

## What it does

Safewalk routes you on the safest walk between two points (MARTA stop → job, home → store, etc.), not just the fastest. You see a default route and a safer route side by side, with per-segment safety coloring and a short explanation of why one is better.

Three sliders weight **sidewalks**, **safety**, and **comfort**. A **wheelchair-accessible** toggle hard-avoids stairs, `wheelchair=no` ways, and grades > 10%. A **light/dark theme** toggle doubles as a day/night routing profile — dark mode shifts the slider defaults toward safety.

If there's no good path, users can upload a photo of the gap. The backend runs the photo through Gemini vision; if it confirms a real pedestrian hazard, the pin is uploaded to Supabase and appears live on every open map and the `/status` workflow dashboard.

## How it's built

- **Frontend (`frontend/`):** Next.js 15 + React 19 + TypeScript. **MapLibre GL JS** rendering OpenFreeMap tiles. Mapbox is optional and only used for geocoding autocomplete (falls back to OSM Nominatim). Supabase JS client for the realtime gap-pin subscription.

- **Backend (`backend/`):** FastAPI + GeoPandas/Shapely. `GET /route` runs an in-process Dijkstra over the walkable subgraph of `outputs/scored_segments.parquet` (~30k segments). `POST /score` wraps Mapbox Directions and ranks the alternatives it returns against the same scored segments. An OSRM walking fallback in the browser handles OD pairs outside the corridor.

- **Pre-bake pipeline (`scripts/prebake.py`):** pulls OSM via Overpass, the ARC Clayton sidewalk inventory, GDOT 2008–2017 AADT counts, GDOT pedestrian crashes (Clayton filter), Meta/WRI canopy-height COG tiles from S3, USGS 3DEP DEM tiles from S3, OpenMeteo ERA5 summer temperatures, FEMA NFHL flood-zone polygons, and Atlanta 311 sidewalk reports. Each module emits a clean `[0, 1]` Series indexed by `segment_id`; the orchestrator joins them onto the segmentized walk network and writes `outputs/scored_segments.parquet` + a sidecar JSON with per-column stats and canary warnings.

- **Storage:** Supabase Postgres + PostGIS. `gap_reports` has a 3-stage workflow (`reported → in_progress → processed`), generated `lng`/`lat` columns so realtime INSERT payloads carry plottable coordinates, and a public `gap-photos` Storage bucket. The realtime publication is on, so new pins appear without a refresh.

## Repo layout

```
safewalk/
├── DESIGN.md                 # engineering spec
├── corridor.json             # locked corridor bbox + primary destination (Gillem)
├── render.yaml               # Render web-service config for the backend
├── requirements.txt          # Python deps for the prebake pipeline
├── network/                  # OSM corpus → walk network builder
│   ├── overpass.py           #   Overpass fetch + cache + parse
│   └── build.py              #   ways → segmentize@25m → split at junctions
├── scripts/
│   ├── prebake.py            #   canonical orchestrator → outputs/scored_segments.parquet
│   ├── validate_corridor.py  #   hour-1 corridor sanity report (walk length, sidewalk shares)
│   └── spot_check.py         #   factor ground-truth picker (Street View URLs)
├── backend/
│   ├── app/                  #   FastAPI scoring + routing service
│   │   ├── main.py
│   │   ├── routes.py         #     /health, /score, /route, /gap-reports, /verify-gap, ...
│   │   ├── scoring.py        #     3-slider model + sub-weights + step-free hard-avoids
│   │   ├── network.py        #     GraphRouter (Dijkstra over walkable subgraph)
│   │   ├── segments.py       #     parquet → SegmentStore + route snap
│   │   ├── directions.py     #     Mapbox Directions wrapper for /score
│   │   └── gap_reports.py    #     Gemini photo verification + Supabase insert
│   ├── layers/               #   per-factor scoring modules (one file per factor)
│   ├── supabase/             #   schema.sql + migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                 # Next.js 15 + MapLibre UI (regular directory, NOT a submodule)
│   └── app/
│       ├── page.tsx          #   Map + sliders + route comparison
│       ├── report/           #   Submit a gap (photo → /analyze-gap → /submit-gap)
│       ├── status/           #   Live workflow dashboard
│       ├── about/
│       └── components/RealMap.tsx
├── data/                     # cached external data (OSM, ARC sidewalks, GDOT AADT)
└── outputs/                  # scored_segments.parquet + sidecar JSON
```

`backend/data/` is gitignored — large crash/311 source files live there locally. The canonical baked parquet ships at `outputs/scored_segments.parquet` and is copied into the Docker image at build time.

## Build the scored corridor

```bash
pip install -r requirements.txt
pip install -r backend/requirements.txt

python scripts/prebake.py
# → outputs/scored_segments.parquet            (30k+ scored segments)
# → outputs/scored_segments.meta.json          (per-column stats + canary warnings)

# offline iteration without R4 factor modules:
python scripts/prebake.py --no-r4
# skip a specific column:
python scripts/prebake.py --skip crash_norm
```

Re-run is idempotent and atomic (tmpfile → `os.replace`). The sidecar JSON surfaces any factor module that silently fell back to its null default.

## Run the backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # fill in tokens
uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

Environment variables (see `backend/.env.example`):
- `MAPBOX_ACCESS_TOKEN` — required only for `POST /score`. `GET /route` doesn't need it.
- `SCORED_SEGMENTS_PATH` — defaults to `outputs/scored_segments.parquet` (resolved relative to the repo root). If absent, `GET /route` returns 503.
- `CORS_ORIGINS` — comma-separated allowed origins.
- `SUPABASE_URL`, `SUPABASE_KEY` — required for `/gap-reports`, `/analyze-gap`, `/submit-gap`, `/verify-gap`.
- `GEMINI_API_KEY` (+ optional `GEMINI_MODEL`, default `gemini-2.5-flash`) — required for photo verification.
- `GAP_PHOTOS_BUCKET` — Supabase Storage bucket name (default `gap-photos`).

Apply `backend/supabase/schema.sql` then `migrations/0001_*.sql` and `0002_*.sql`.

## Run the frontend

```bash
cd frontend
npm install
cp .env.example .env.local                            # fill in tokens
npm run dev
```

Environment variables (see `frontend/.env.example`):
- `NEXT_PUBLIC_SAFEWALK_API_URL` — base URL of the FastAPI backend.
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — power the live gap-pin map and realtime subscriptions.
- `NEXT_PUBLIC_MAPBOX_TOKEN` — optional; enables Mapbox geocoding autocomplete. Without it the search box falls back to OSM Nominatim. The map itself uses MapLibre GL JS with OpenFreeMap's `liberty` style and does not require a Mapbox token.

## Demo corridor

Gillem Logistics Center, Forest Park GA. Bbox + primary destination locked in `corridor.json`:

```json
{
  "name": "gillem-logistics-corridor",
  "bbox": [-84.37, 33.58, -84.29, 33.65],
  "primary_destination": { "name": "Gillem Logistics Center entrance", "lonlat": [-84.3289, 33.6202] }
}
```

The Forest Park / Lake City area is in **Clayton County**, outside the City of Atlanta — so the ATL311 layer is empty here by design and the live `gap_reports` table is the operative hazard source.
