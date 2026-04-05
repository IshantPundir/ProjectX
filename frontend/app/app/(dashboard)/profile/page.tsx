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

/* ─── Icons ─── */

function IconUser({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
    </svg>
  );
}

function IconMail({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
    </svg>
  );
}

function IconBuilding({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" />
    </svg>
  );
}

function IconShield({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
    </svg>
  );
}

function IconLogout({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
    </svg>
  );
}

/* ─── Page ─── */

export default function ProfilePage() {
  const router = useRouter();
  const [me, setMe] = useState<MeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [signingOut, setSigningOut] = useState(false);

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
    setSigningOut(true);
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  if (loading) {
    return (
      <div className="max-w-2xl space-y-4">
        <div className="h-6 w-32 bg-zinc-100 rounded animate-pulse" />
        <div className="h-48 bg-zinc-100 rounded-xl animate-pulse" />
        <div className="h-32 bg-zinc-100 rounded-xl animate-pulse" />
      </div>
    );
  }

  if (!me) {
    return (
      <div className="max-w-2xl">
        <p className="text-sm text-zinc-500">Not logged in.</p>
      </div>
    );
  }

  // Group assignments by org unit
  const byUnit: Record<string, { name: string; roles: string[] }> = {};
  for (const a of me.assignments) {
    if (!byUnit[a.org_unit_id]) {
      byUnit[a.org_unit_id] = { name: a.org_unit_name, roles: [] };
    }
    byUnit[a.org_unit_id].roles.push(a.role_name);
  }

  // Collect unique permissions across all assignments
  const allPermissions = [...new Set(me.assignments.flatMap((a) => a.permissions))].sort();

  // Initials for avatar
  const initials = me.full_name
    ? me.full_name
        .split(" ")
        .map((w) => w[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : me.email[0].toUpperCase();

  return (
    <div className="max-w-2xl">
      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Profile</h1>

      {/* ─── User card ─── */}
      <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden mb-6">
        {/* Header with avatar */}
        <div className="bg-gradient-to-r from-green-600 to-green-500 px-6 py-8">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center text-white text-xl font-semibold shrink-0">
              {initials}
            </div>
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-white truncate">
                {me.full_name || me.email}
              </h2>
              {me.full_name && (
                <p className="text-sm text-green-100 truncate">{me.email}</p>
              )}
              <div className="flex items-center gap-2 mt-1.5">
                {me.is_super_admin && (
                  <span className="inline-flex items-center gap-1 bg-white/20 text-white px-2 py-0.5 rounded-full text-xs font-medium">
                    <IconShield className="w-3 h-3" />
                    Super Admin
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Details */}
        <div className="px-6 py-5">
          <div className="grid grid-cols-2 gap-6">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0 mt-0.5">
                <IconMail className="w-4 h-4 text-zinc-500" />
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-zinc-400">Email</p>
                <p className="text-sm text-zinc-900 truncate">{me.email}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0 mt-0.5">
                <IconBuilding className="w-4 h-4 text-zinc-500" />
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-zinc-400">Organization</p>
                <p className="text-sm text-zinc-900 truncate">{me.client_name}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0 mt-0.5">
                <IconUser className="w-4 h-4 text-zinc-500" />
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-zinc-400">Full Name</p>
                <p className="text-sm text-zinc-900">{me.full_name || "Not set"}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0 mt-0.5">
                <IconShield className="w-4 h-4 text-zinc-500" />
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-zinc-400">Account Type</p>
                <p className="text-sm text-zinc-900">
                  {me.is_super_admin ? "Super Admin" : "Team Member"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Assignments ─── */}
      {Object.keys(byUnit).length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
          <h2 className="text-sm font-semibold text-zinc-900 mb-4">
            Role Assignments
            <span className="text-zinc-400 font-normal ml-1.5">
              ({me.assignments.length} role{me.assignments.length !== 1 ? "s" : ""} across{" "}
              {Object.keys(byUnit).length} unit{Object.keys(byUnit).length !== 1 ? "s" : ""})
            </span>
          </h2>
          <div className="space-y-3">
            {Object.entries(byUnit).map(([unitId, { name, roles }]) => (
              <div
                key={unitId}
                className="flex items-center justify-between border border-zinc-100 rounded-lg px-4 py-3 hover:border-zinc-200 transition-colors duration-100"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-zinc-900">{name}</p>
                </div>
                <div className="flex flex-wrap gap-1.5 shrink-0 ml-4">
                  {roles.map((role) => (
                    <span
                      key={role}
                      className={`px-2 py-0.5 rounded text-xs font-medium ${
                        role === "Admin"
                          ? "bg-blue-50 text-blue-700"
                          : role === "Recruiter"
                            ? "bg-green-50 text-green-700"
                            : role === "Hiring Manager"
                              ? "bg-amber-50 text-amber-700"
                              : role === "Interviewer"
                                ? "bg-purple-50 text-purple-700"
                                : "bg-zinc-100 text-zinc-600"
                      }`}
                    >
                      {role}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No assignments message */}
      {!me.is_super_admin && me.assignments.length === 0 && (
        <div className="bg-white border border-dashed border-zinc-200 rounded-xl p-8 text-center mb-6">
          <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mx-auto mb-3">
            <IconShield className="w-5 h-5 text-zinc-400" />
          </div>
          <p className="text-sm text-zinc-500 mb-1">No roles assigned yet</p>
          <p className="text-xs text-zinc-400">
            Contact your administrator to get assigned to an organizational unit.
          </p>
        </div>
      )}

      {/* ─── Permissions summary ─── */}
      {allPermissions.length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
          <h2 className="text-sm font-semibold text-zinc-900 mb-3">
            Permissions
            <span className="text-zinc-400 font-normal ml-1.5">({allPermissions.length})</span>
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {allPermissions.map((p) => (
              <span
                key={p}
                className="bg-zinc-50 text-zinc-600 border border-zinc-100 px-2 py-0.5 rounded text-xs"
              >
                {p}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ─── Sign out ─── */}
      <div className="border-t border-zinc-200 pt-6">
        <button
          onClick={handleSignOut}
          disabled={signingOut}
          className="inline-flex items-center gap-2 text-sm text-red-600 hover:text-red-700 cursor-pointer transition-colors duration-150 disabled:opacity-50"
        >
          <IconLogout className="w-4 h-4" />
          {signingOut ? "Signing out..." : "Sign out"}
        </button>
      </div>
    </div>
  );
}
