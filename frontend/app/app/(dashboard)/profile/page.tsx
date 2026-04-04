"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface RoleAssignment {
  org_unit_id: string;
  org_unit_name: string;
  role_name: string;
  permissions: string[];
}

interface MeData {
  user_id: string;
  email: string;
  full_name: string | null;
  tenant_id: string;
  client_name: string;
  is_super_admin: boolean;
  assignments: RoleAssignment[];
}

export default function ProfilePage() {
  const router = useRouter();
  const [me, setMe] = useState<MeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const supabase = createClient();
        const {
          data: { session },
        } = await supabase.auth.getSession();
        if (!session?.access_token) return;
        const data = await apiFetch<MeData>("/api/auth/me", {
          token: session.access_token,
        });
        setMe(data);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  if (loading) return <p className="text-sm text-zinc-500">Loading...</p>;
  if (!me) return <p className="text-sm text-zinc-500">Not logged in.</p>;

  // Group assignments by org unit
  const byUnit: Record<string, { name: string; roles: string[] }> = {};
  for (const a of me.assignments) {
    if (!byUnit[a.org_unit_id]) {
      byUnit[a.org_unit_id] = { name: a.org_unit_name, roles: [] };
    }
    byUnit[a.org_unit_id].roles.push(a.role_name);
  }

  return (
    <>
      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Profile</h1>

      <div className="bg-white border border-zinc-200 rounded-lg p-6 max-w-lg space-y-4">
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Email</p>
          <p className="text-sm text-zinc-900">{me.email}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Name</p>
          <p className="text-sm text-zinc-900">{me.full_name || "—"}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Role</p>
          <div className="flex items-center gap-2">
            {me.is_super_admin && (
              <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-xs font-medium">
                Super Admin
              </span>
            )}
            {!me.is_super_admin && me.assignments.length === 0 && (
              <p className="text-sm text-zinc-400">
                No roles assigned yet. Contact your administrator.
              </p>
            )}
          </div>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">
            Organization
          </p>
          <p className="text-sm text-zinc-900">{me.client_name}</p>
        </div>

        {Object.keys(byUnit).length > 0 && (
          <div>
            <p className="text-xs font-medium text-zinc-500 mb-1.5">
              Assignments
            </p>
            <div className="space-y-2">
              {Object.entries(byUnit).map(([unitId, { name, roles }]) => (
                <div
                  key={unitId}
                  className="bg-zinc-50 rounded-lg px-3 py-2"
                >
                  <p className="text-sm font-medium text-zinc-800">{name}</p>
                  <p className="text-xs text-zinc-500">{roles.join(", ")}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <button
        onClick={handleSignOut}
        className="mt-6 text-sm text-red-600 hover:underline"
      >
        Sign out
      </button>
    </>
  );
}
