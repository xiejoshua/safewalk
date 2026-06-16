-- 0002_gap_report_status_workflow.sql
-- Replaces the open/resolved status with a 3-stage workflow:
--   reported    — freshly submitted (default for every new report)
--   in_progress — acknowledged / being worked on
--   processed   — resolved
--
-- New reports default to 'reported'. Existing rows are randomized across the
-- three statuses (per request) so the status page has a realistic spread.
--
-- Idempotent-ish: safe to re-run, though the randomize UPDATE will reshuffle
-- existing rows each time. Apply in the Supabase SQL editor.

-- Drop the old constraint first so the randomize UPDATE doesn't violate it.
alter table public.gap_reports drop constraint if exists gap_reports_status_check;

-- New default for incoming reports.
alter table public.gap_reports alter column status set default 'reported';

-- Randomize the rows that already exist.
update public.gap_reports
set status = (array['reported', 'in_progress', 'processed'])[floor(random() * 3)::int + 1];

-- Enforce the new vocabulary going forward.
alter table public.gap_reports
    add constraint gap_reports_status_check
    check (status in ('reported', 'in_progress', 'processed'));
