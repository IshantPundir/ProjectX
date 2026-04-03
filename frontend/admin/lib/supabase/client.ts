import { createBrowserClient } from "@supabase/ssr";

// Use a unique storage key so admin and client apps maintain
// independent sessions on localhost (cookies are domain-scoped,
// not port-scoped). In production, different domains handle this.
const STORAGE_KEY = "sb-admin-auth-token";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { storageKey: STORAGE_KEY } },
  );
}
