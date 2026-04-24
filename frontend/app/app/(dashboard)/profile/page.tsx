"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { authApi, type MeResponse } from "@/lib/api/auth";

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
  const [me, setMe] = useState<MeResponse | null>(null);
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
        const data = await authApi.me(session.access_token);
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
    <div className="mx-auto max-w-[800px] px-8 pb-10 pt-5">
      <h1
        className="px-serif m-0 mb-6 text-[30px] font-normal"
        style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
      >
        Profile
      </h1>

      {/* ─── User card ─── */}
      <div
        className="mb-6 overflow-hidden rounded-[10px] border"
        style={{
          background: "var(--px-surface)",
          borderColor: "var(--px-hairline)",
        }}
      >
        {/* Header with avatar */}
        <div
          className="px-6 py-8"
          style={{
            background:
              "linear-gradient(135deg, var(--px-accent) 0%, var(--px-accent-2) 100%)",
          }}
        >
          <div className="flex items-center gap-4">
            <div
              className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-xl font-semibold text-white backdrop-blur-sm"
              style={{ background: "rgba(255,255,255,0.18)" }}
            >
              {initials}
            </div>
            <div className="min-w-0">
              <h2
                className="px-serif truncate text-[22px] font-normal text-white"
                style={{ letterSpacing: "-0.4px" }}
              >
                {me.full_name || me.email}
              </h2>
              {me.full_name && (
                <p
                  className="truncate text-[13px]"
                  style={{ color: "rgba(255,255,255,0.75)" }}
                >
                  {me.email}
                </p>
              )}
              <div className="mt-1.5 flex items-center gap-2">
                {me.is_super_admin && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium text-white"
                    style={{ background: "rgba(255,255,255,0.18)" }}
                  >
                    <IconShield className="h-3 w-3" />
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
            {[
              { icon: IconMail, label: "Email", value: me.email },
              { icon: IconBuilding, label: "Organization", value: me.client_name },
              { icon: IconUser, label: "Full name", value: me.full_name || "Not set" },
              {
                icon: IconShield,
                label: "Account type",
                value: me.is_super_admin ? "Super Admin" : "Team Member",
              },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="flex items-start gap-3">
                <div
                  className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
                  style={{ background: "var(--px-surface-2)" }}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p
                    className="text-[10.5px] font-semibold uppercase"
                    style={{ letterSpacing: "1.1px", color: "var(--px-fg-4)" }}
                  >
                    {label}
                  </p>
                  <p
                    className="truncate text-[13px]"
                    style={{ color: "var(--px-fg)" }}
                  >
                    {value}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ─── Assignments ─── */}
      {Object.keys(byUnit).length > 0 && (
        <div
          className="mb-6 rounded-[10px] border p-6"
          style={{
            background: "var(--px-surface)",
            borderColor: "var(--px-hairline)",
          }}
        >
          <h2
            className="mb-4 text-[11px] font-semibold uppercase"
            style={{ letterSpacing: "1.1px", color: "var(--px-fg-4)" }}
          >
            Role assignments
            <span
              className="ml-1.5 font-normal normal-case"
              style={{ letterSpacing: 0, color: "var(--px-fg-4)" }}
            >
              ({me.assignments.length} role{me.assignments.length !== 1 ? "s" : ""} across{" "}
              {Object.keys(byUnit).length} unit{Object.keys(byUnit).length !== 1 ? "s" : ""})
            </span>
          </h2>
          <div className="space-y-2">
            {Object.entries(byUnit).map(([unitId, { name, roles }]) => (
              <div
                key={unitId}
                className="flex items-center justify-between rounded-lg border px-4 py-3 transition-colors"
                style={{ borderColor: "var(--px-hairline)" }}
              >
                <div className="min-w-0">
                  <p
                    className="text-[13px] font-medium"
                    style={{ color: "var(--px-fg)" }}
                  >
                    {name}
                  </p>
                </div>
                <div className="ml-4 flex shrink-0 flex-wrap gap-1.5">
                  {roles.map((role) => {
                    const chipClass =
                      role === "Admin"
                        ? "px-chip ai"
                        : role === "Recruiter"
                          ? "px-chip ok"
                          : role === "Hiring Manager"
                            ? "px-chip caution"
                            : role === "Interviewer"
                              ? "px-chip human"
                              : "px-chip soft";
                    return (
                      <span key={role} className={chipClass}>
                        {role}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No assignments message */}
      {!me.is_super_admin && me.assignments.length === 0 && (
        <div
          className="mb-6 rounded-[10px] border border-dashed p-8 text-center"
          style={{ borderColor: "var(--px-hairline-strong)" }}
        >
          <div
            className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full"
            style={{ background: "var(--px-surface-2)" }}
          >
            <IconShield className="h-5 w-5 opacity-60" />
          </div>
          <p className="mb-1 text-sm" style={{ color: "var(--px-fg-3)" }}>
            No roles assigned yet
          </p>
          <p className="text-[12px]" style={{ color: "var(--px-fg-4)" }}>
            Contact your administrator to get assigned to an organizational unit.
          </p>
        </div>
      )}

      {/* ─── Sign out ─── */}
      <div className="border-t pt-6" style={{ borderColor: "var(--px-hairline)" }}>
        <button
          onClick={handleSignOut}
          disabled={signingOut}
          className="inline-flex cursor-pointer items-center gap-2 text-sm transition-colors disabled:opacity-50"
          style={{ color: "var(--px-danger)" }}
        >
          <IconLogout className="h-4 w-4" />
          {signingOut ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}
