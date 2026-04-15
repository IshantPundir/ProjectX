import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { cache } from "react";
import SidebarNav from "./SidebarNav";
import { DashboardProviders } from "@/components/dashboard/providers";

const getMe = cache(async (token: string, apiUrl: string) => {
  const res = await fetch(`${apiUrl}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json() as Promise<{
    is_super_admin: boolean;
    onboarding_complete: boolean;
    has_org_units: boolean;
    workspace_mode: string;
  }>;
});

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = await createClient();

  // AUTH GATE — cryptographically validated.
  // getUser() hits the Supabase Auth server and verifies the JWT signature.
  // This is the ONLY call that gates access to the dashboard; if this fails
  // or returns no user, we redirect to /login and never reach the token
  // extraction below.
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error || !user) {
    redirect("/login");
  }

  // TOKEN EXTRACTION — not an authorization check.
  // @supabase/ssr v0.10 / auth-js: getUser() returns { user } only — it
  // does NOT expose access_token. We need the token to forward to the
  // FastAPI backend, so we read it from getSession().
  //
  // Per the project's memory note `feedback_middleware_jwt_claims.md`,
  // getSession() reads the token from cookies WITHOUT server-side
  // validation and must never be used as the sole authorization gate.
  // It is safe here because the getUser() call above has already
  // cryptographically validated the session. The only job of this call
  // is to pull the already-validated token out so we can forward it to
  // Nexus, which re-validates it on every request anyway.
  //
  // If a future auth-js version exposes access_token on the getUser()
  // response, collapse these two calls into one.
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session?.access_token) {
    redirect("/login");
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
  const me = await getMe(session.access_token, apiUrl);

  if (me && me.is_super_admin && !me.onboarding_complete) {
    redirect("/onboarding");
  }

  return (
    <div className="flex flex-1">
      <aside className="w-56 border-r border-zinc-200 bg-white p-4 flex flex-col">
        <h2 className="text-sm font-bold text-zinc-900 mb-6">ProjectX</h2>
        <SidebarNav userEmail={user.email ?? ""} />
      </aside>
      <main className="flex-1 p-6">
        <DashboardProviders>{children}</DashboardProviders>
      </main>
    </div>
  );
}
