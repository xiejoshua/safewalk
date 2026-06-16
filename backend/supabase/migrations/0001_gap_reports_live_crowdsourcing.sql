-- 0001_gap_reports_live_crowdsourcing.sql
-- Extends the gap_reports table for the live photo-report pipeline:
--   1. photo_url        — public URL of the AI-verified gap photo (Supabase Storage)
--   2. lng / lat        — plain coordinates so the frontend can render pins without
--                         parsing the geography column. Stored generated columns are
--                         included in realtime INSERT payloads, so live pins get them too.
--   3. gap-photos bucket — public Storage bucket the backend uploads verified photos to.
--
-- Idempotent: safe to re-run. Apply in the Supabase SQL editor (or `supabase db push`).

-- ── Columns ───────────────────────────────────────────────────────────────────

alter table public.gap_reports
    add column if not exists photo_url text;

-- geom is geography(Point, 4326); cast to geometry for st_x / st_y.
alter table public.gap_reports
    add column if not exists lng double precision
    generated always as (st_x(geom::geometry)) stored;

alter table public.gap_reports
    add column if not exists lat double precision
    generated always as (st_y(geom::geometry)) stored;

-- ── Storage bucket for verified gap photos ────────────────────────────────────

insert into storage.buckets (id, name, public)
values ('gap-photos', 'gap-photos', true)
on conflict (id) do nothing;

-- Anyone may read photos (public hazard evidence shown on the map).
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'storage' and tablename = 'objects'
          and policyname = 'gap_photos_public_read'
    ) then
        create policy "gap_photos_public_read"
            on storage.objects for select
            using (bucket_id = 'gap-photos');
    end if;
end;
$$;

-- The anon role may upload (the backend uploads with the anon key; the service-role
-- key bypasses RLS entirely, so this only matters when an anon key is used).
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'storage' and tablename = 'objects'
          and policyname = 'gap_photos_anon_insert'
    ) then
        create policy "gap_photos_anon_insert"
            on storage.objects for insert
            to anon
            with check (bucket_id = 'gap-photos');
    end if;
end;
$$;
