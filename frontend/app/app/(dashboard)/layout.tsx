import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { cache } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
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
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error || !user) {
    redirect("/login");
  }

  // TOKEN EXTRACTION — not an authorization check. See historical note
  // in git history if changing this pattern.
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
    <div className="flex h-screen w-full flex-1">
      <AppShell userEmail={user.email ?? ""}>
        <DashboardProviders>{children}</DashboardProviders>
      </AppShell>
    </div>
  );
}
