-- gap_reports: Crowdsourced sidewalk hazard reports
-- CRS: WGS84 (EPSG:4326) — PostGIS geography type for metre-accurate distance queries

-- Enable required extensions (idempotent in Supabase)
create extension if not exists postgis;

-- ── Table ────────────────────────────────────────────────────────────────────

create table if not exists public.gap_reports (
    id          uuid primary key default gen_random_uuid(),
    geom        geography(Point, 4326) not null,
    type        text not null check (type in (
                    'broken_sidewalk',
                    'no_sidewalk',
                    'no_crossing',
                    'obstruction',
                    'streetlight_out',
                    'other'
                )),
    note        text,
    photo_url   text,
    reported_at timestamptz not null default now(),
    status      text not null default 'open' check (status in ('open', 'resolved'))
);

-- Plain coordinates so the frontend can render pins without parsing the geography
-- column. Stored generated columns appear in realtime INSERT payloads too.
-- (For an already-deployed table, see migrations/0001_gap_reports_live_crowdsourcing.sql.)
alter table public.gap_reports
    add column if not exists lng double precision
    generated always as (st_x(geom::geometry)) stored;
alter table public.gap_reports
    add column if not exists lat double precision
    generated always as (st_y(geom::geometry)) stored;

-- ── Spatial index ─────────────────────────────────────────────────────────────

create index if not exists gap_reports_geom_idx
    on public.gap_reports using gist (geom);

-- ── Row Level Security ────────────────────────────────────────────────────────

alter table public.gap_reports enable row level security;

-- Anonymous users may submit reports (no auth required for MVP)
create policy "anon_insert"
    on public.gap_reports
    for insert
    to anon
    with check (true);

-- Anyone may read reports (public hazard data)
create policy "anon_select"
    on public.gap_reports
    for select
    to anon
    using (true);

-- ── Realtime publication ──────────────────────────────────────────────────────

-- Add gap_reports to the default Supabase realtime publication so clients
-- receive live INSERT events when new pins are dropped.
-- NOTE: run this AFTER the table exists; idempotent via DO block.
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and tablename = 'gap_reports'
    ) then
        alter publication supabase_realtime add table public.gap_reports;
    end if;
end;
$$;
