# Contract: gap_reports Supabase API (R4 ↔ R1)

**Parties**: R4 (schema/seed owner) ↔ R1 (frontend consumer)

**Locked at kickoff**: 2026-06-14

---

## Table: gap_reports

See [data-model.md](../data-model.md) for the full SQL schema.

---

## REST API (Supabase auto-generated)

Base URL: `https://<project>.supabase.co/rest/v1/gap_reports`

### INSERT — submit a gap report

```
POST /rest/v1/gap_reports
Authorization: Bearer <SUPABASE_ANON_KEY>
Content-Type: application/json
Prefer: return=minimal

{
  "geom": "SRID=4326;POINT(-84.3921 33.6958)",
  "type": "no_sidewalk",
  "note": "No sidewalk from bus stop to Gillem gate",
  "photo_url": null
}
```

**Valid `type` values**: `no_sidewalk`, `no_crossing`, `broken_sidewalk`,
`obstruction`, `streetlight_out`, `other`

**Response**: `201 Created` (no body when `Prefer: return=minimal`)

### SELECT — fetch all reports (dashboard / prebake)

```
GET /rest/v1/gap_reports?select=id,geom,type,note,created_at&order=created_at.desc
Authorization: Bearer <SUPABASE_ANON_KEY>
```

**Response**: JSON array of report objects.

---

## Realtime subscription (R1 frontend)

R1 subscribes to the `gap_reports` Postgres changes channel using the Supabase JS
client. New INSERTs appear within ~1 second of commit.

```js
// frontend — example channel subscription
supabase
  .channel('gap_reports')
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'gap_reports' },
    (payload) => addPinToMap(payload.new))
  .subscribe()
```

---

## RLS policy (applied by R4 in schema.sql)

```sql
-- Anon can insert and read; no update/delete
alter table gap_reports enable row level security;

create policy "anon_insert" on gap_reports for insert to anon with check (true);
create policy "anon_select" on gap_reports for select to anon using (true);
```

---

## Environment variables (frontend + prebake)

| Variable | Value source | Used by |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project settings | R1 frontend |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase project settings | R1 frontend |
| `SUPABASE_URL` | Same value | R4 prebake (hazards.py) |
| `SUPABASE_ANON_KEY` | Same value | R4 prebake (hazards.py) |
