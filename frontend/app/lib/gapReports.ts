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

// Fetch all existing reports (the pins already on the map for known problems).
export async function fetchGapReports(): Promise<GapReport[]> {
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
        const row = payload.new as GapReport;
        if (row?.lng != null && row?.lat != null) onInsert(row);
      }
    )
    .subscribe();

  return () => {
    supabase.removeChannel(channel);
  };
}
