import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { cache } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { DashboardProviders } from "@/components/dashboard/providers";
import { authApi, type MeResponse } from "@/lib/api/auth";

const getMe = cache(async (token: string): Promise<MeResponse | null> => {
  try {
    return await authApi.me(token);
  } catch {
    return null;
  }
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

  const me = await getMe(session.access_token);

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
