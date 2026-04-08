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
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error || !user) {
    redirect("/login");
  }

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
