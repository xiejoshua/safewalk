import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Lazily-created browser Supabase client. Returns null when env vars are absent so
// the app still renders (the map just shows no live pins) without Supabase configured.
let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (client) return client;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return null;

  client = createClient(url, anonKey, {
    auth: { persistSession: false },
    realtime: { params: { eventsPerSecond: 5 } }
  });
  return client;
}
