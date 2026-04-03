import { createBrowserClient } from "@supabase/ssr";

const STORAGE_KEY = "sb-app-auth-token";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { storageKey: STORAGE_KEY } },
  );
}
