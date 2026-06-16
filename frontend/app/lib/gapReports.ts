import { getSupabase } from "./supabase";

export type GapReport = {
  id: string;
  type: string;
  note: string | null;
  photo_url: string | null;
  lng: number;
  lat: number;
  status: string | null;
  reported_at: string | null;
};

// Human labels + pin colors for each hazard type baked into gap_reports.
export const GAP_TYPE_META: Record<string, { label: string; color: string }> = {
  broken_sidewalk: { label: "Broken sidewalk", color: "#c0392b" },
  no_sidewalk: { label: "No sidewalk", color: "#e76f2e" },
  no_crossing: { label: "Unsafe crossing", color: "#e8c547" },
  obstruction: { label: "Obstruction", color: "#9b59b6" },
  streetlight_out: { label: "Streetlight out", color: "#3d6fb4" },
  other: { label: "Hazard", color: "#7f8c8d" }
};

export function gapTypeMeta(type: string) {
  return GAP_TYPE_META[type] ?? GAP_TYPE_META.other;
}

// Workflow status → label + pin color. Matches migration 0002's vocabulary.
export type GapStatus = "reported" | "in_progress" | "processed";

export const STATUS_META: Record<string, { label: string; color: string }> = {
  reported: { label: "Reported", color: "#e23d28" },
  in_progress: { label: "In progress", color: "#e8a33d" },
  processed: { label: "Processed", color: "#2d9e5e" }
};

export const STATUS_ORDER: GapStatus[] = ["reported", "in_progress", "processed"];

export function statusMeta(status: string | null | undefined) {
  return STATUS_META[status ?? ""] ?? STATUS_META.reported;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_SAFEWALK_API_URL;

// Fetch all existing reports (the pins already on the map for known problems).
// Prefers the backend /gap-reports endpoint so the browser isn't reading the DB
// directly; falls back to a direct Supabase read only if the backend isn't set.
export async function fetchGapReports(): Promise<GapReport[]> {
  if (API_BASE_URL) {
    try {
      const res = await fetch(`${API_BASE_URL}/gap-reports`, { cache: "no-store" });
      if (res.ok) {
        const rows = (await res.json()) as GapReport[];
        return rows.filter((r) => r.lng != null && r.lat != null);
      }
      console.warn("fetchGapReports: backend returned", res.status);
    } catch (err) {
      console.warn("fetchGapReports: backend unreachable", err);
    }
  }

  const supabase = getSupabase();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from("gap_reports")
    .select("id,type,note,photo_url,lng,lat,status,reported_at")
    .order("reported_at", { ascending: false });

  if (error) {
    console.warn("fetchGapReports failed:", error.message);
    return [];
  }
  return (data ?? []).filter((r): r is GapReport => r.lng != null && r.lat != null);
}

// Subscribe to live INSERTs so newly reported pins appear on every open map in ~1s.
// Returns an unsubscribe function.
export function subscribeGapReports(onInsert: (report: GapReport) => void): () => void {
  const supabase = getSupabase();
  if (!supabase) return () => {};

  const channel = supabase
    .channel("gap_reports_live")
    .on(
      "postgres_changes",
      { event: "INSERT", schema: "public", table: "gap_reports" },
      (payload) => {
        const raw = payload.new as Partial<GapReport> & { id: string | number };
        if (raw?.lng == null || raw?.lat == null) return;
        // Normalize id to a string so it dedupes against backend-fetched rows.
        onInsert({ ...(raw as GapReport), id: String(raw.id) });
      }
    )
    .subscribe();

  return () => {
    supabase.removeChannel(channel);
  };
}

// Move a report between workflow statuses. Returns the updated row.
export async function updateGapStatus(id: string, status: GapStatus): Promise<GapReport> {
  if (!API_BASE_URL) throw new Error("Backend not configured (NEXT_PUBLIC_SAFEWALK_API_URL).");
  const res = await fetch(`${API_BASE_URL}/gap-reports/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status })
  });
  if (!res.ok) {
    let detail = "Failed to update status";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<GapReport>;
}
