import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { cache } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { DashboardProviders } from "@/components/dashboard/providers";
import { authApi, type MeResponse } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

const getMe = cache(async (token: string): Promise<MeResponse> => authApi.me(token));

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

  let me: MeResponse | null = null;
  let suspended = false;
  try {
    me = await getMe(session.access_token);
  } catch (err) {
    if (err instanceof ApiError && err.code === "ACCOUNT_SUSPENDED") {
      suspended = true;
    }
    // Any other error: render the shell with me=null and let downstream
    // queries surface the failure — same behaviour as before.
  }

  if (suspended) {
    redirect("/suspended");
  }

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
