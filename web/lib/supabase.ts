import "server-only";
import { createClient } from "@supabase/supabase-js";

const url = process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!url || !key) {
  throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY must be set");
}

// Server-only client (service role bypasses RLS). Never import this from a client component.
export const supabase = createClient(url, key, { auth: { persistSession: false } });
