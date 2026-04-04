"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface MeData {
  is_admin: boolean;
  permissions: string[];
  role: string;
}

interface OrgUnit {
  id: string;
  name: string;
  unit_type: string;
}

interface TeamMember {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_admin: boolean;
  permissions: string[];
  source: "user" | "invite";
  status: string;
  created_at: string;
}

const ALL_PERMISSIONS = [
  "users.invite_admins", "users.invite_users", "users.deactivate",
  "org_units.create", "org_units.manage",
  "jobs.create", "jobs.manage",
  "candidates.view", "candidates.evaluate", "candidates.advance",
  "interviews.schedule", "interviews.conduct",
  "reports.view", "reports.export",
  "settings.client", "settings.integrations",
];

export default function TeamPage() {
  const [me, setMe] = useState<MeData | null>(null);
  const [orgUnits, setOrgUnits] = useState<OrgUnit[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("Recruiter");
  const [inviteAsAdmin, setInviteAsAdmin] = useState(false);
  const [invitePermissions, setInvitePermissions] = useState<string[]>([]);
  const [inviteOrgUnitId, setInviteOrgUnitId] = useState("");
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState("");

  async function getToken() {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      window.location.href = "/login";
      return null;
    }
    return session.access_token;
  }

  async function loadMembers() {
    try {
      const token = await getToken();
      if (!token) return;
      const data = await apiFetch<TeamMember[]>("/api/settings/team/members", { token });
      setMembers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load team");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    async function init() {
      try {
        const token = await getToken();
        if (!token) return;

        const [meData, units] = await Promise.all([
          apiFetch<MeData>("/api/auth/me", { token }),
          apiFetch<OrgUnit[]>("/api/org-units", { token }),
        ]);

        setMe(meData);
        setOrgUnits(units);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load page data");
      }
    }
    init();
    loadMembers();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handlePermissionToggle(perm: string) {
    setInvitePermissions((prev) =>
      prev.includes(perm)
        ? prev.filter((p) => p !== perm)
        : [...prev, perm]
    );
  }

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteLoading(true);
    setError("");
    setInviteSuccess("");

    try {
      const token = await getToken();
      if (!token) return;
      const result = await apiFetch<{ invite_url: string }>("/api/settings/team/invite", {
        method: "POST",
        token,
        body: JSON.stringify({
          email: inviteEmail,
          role: inviteAsAdmin ? "Admin" : inviteRole,
          is_admin: inviteAsAdmin,
          permissions: inviteAsAdmin ? invitePermissions : [],
          org_unit_id: inviteOrgUnitId || null,
        }),
      });

      setInviteEmail("");
      setInvitePermissions([]);
      setInviteOrgUnitId("");
      setInviteAsAdmin(false);
      setInviteRole("Recruiter");
      setInviteSuccess(result.invite_url
        ? `Invite sent! URL: ${result.invite_url}`
        : "Invite sent!");
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send invite");
    } finally {
      setInviteLoading(false);
    }
  }

  async function handleResend(inviteId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/resend/" + inviteId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend");
    }
  }

  async function handleRevoke(inviteId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/revoke/" + inviteId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  }

  async function handleDeactivate(userId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/deactivate/" + userId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate");
    }
  }

  const statusColor: Record<string, string> = {
    active: "bg-green-50 text-green-700",
    inactive: "bg-zinc-100 text-zinc-500",
    pending: "bg-amber-50 text-amber-700",
  };

  const users = members.filter((m) => m.source === "user");
  const invites = members.filter((m) => m.source === "invite");

  const canInviteAdmins = me?.permissions.includes("users.invite_admins") ?? false;
  const myPermissions = me?.permissions ?? [];
  const availablePermissions = ALL_PERMISSIONS.filter((p) => myPermissions.includes(p));

  return (
    <>
      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Team Management</h1>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
      )}
      {inviteSuccess && (
        <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg p-3 mb-4">
          {inviteSuccess}
        </div>
      )}

      <form onSubmit={handleInvite} className="bg-white border border-zinc-200 rounded-lg p-5 mb-6">
        <h2 className="text-sm font-medium text-zinc-900 mb-3">Invite Team Member</h2>
        <div className="flex gap-3 items-end flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-medium text-zinc-600 mb-1">Email</label>
            <input
              type="email"
              required
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
              placeholder="colleague@company.com"
            />
          </div>
          {!inviteAsAdmin && (
            <div className="w-48">
              <label className="block text-xs font-medium text-zinc-600 mb-1">Role</label>
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
              >
                <option value="Recruiter">Recruiter</option>
                <option value="Hiring Manager">Hiring Manager</option>
                <option value="Interviewer">Interviewer</option>
                <option value="Observer">Observer</option>
              </select>
            </div>
          )}
          {inviteAsAdmin && (
            <div className="w-48">
              <label className="block text-xs font-medium text-zinc-600 mb-1">Role</label>
              <div className="border border-zinc-200 rounded-lg px-3 py-2 text-sm bg-zinc-50 text-zinc-500">
                Admin
              </div>
            </div>
          )}
          <div className="w-48">
            <label className="block text-xs font-medium text-zinc-600 mb-1">Org Unit</label>
            <select
              value={inviteOrgUnitId}
              onChange={(e) => setInviteOrgUnitId(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
            >
              <option value="">None</option>
              {orgUnits.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.unit_type})
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={inviteLoading}
            className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
          >
            {inviteLoading ? "Sending..." : "Send Invite"}
          </button>
        </div>

        {canInviteAdmins && (
          <div className="mt-4">
            <label className="flex items-center gap-2 text-sm text-zinc-700 cursor-pointer">
              <input
                type="checkbox"
                checked={inviteAsAdmin}
                onChange={(e) => {
                  setInviteAsAdmin(e.target.checked);
                  if (!e.target.checked) {
                    setInvitePermissions([]);
                  }
                }}
                className="rounded border-zinc-300 text-green-600 focus:ring-green-600"
              />
              Invite as Admin
            </label>
          </div>
        )}

        {inviteAsAdmin && availablePermissions.length > 0 && (
          <div className="mt-4">
            <label className="block text-xs font-medium text-zinc-600 mb-2">Permissions</label>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
              {availablePermissions.map((perm) => (
                <label key={perm} className="flex items-center gap-2 text-sm text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={invitePermissions.includes(perm)}
                    onChange={() => handlePermissionToggle(perm)}
                    className="rounded border-zinc-300 text-green-600 focus:ring-green-600"
                  />
                  {perm}
                </label>
              ))}
            </div>
          </div>
        )}
      </form>

      {loading ? (
        <p className="text-sm text-zinc-500">Loading team...</p>
      ) : (
        <>
          <h2 className="text-sm font-medium text-zinc-900 mb-3">Members ({users.length})</h2>
          {users.length === 0 ? (
            <p className="text-sm text-zinc-500 mb-6">No team members yet.</p>
          ) : (
            <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-zinc-50 border-b border-zinc-200">
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Name</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Permissions</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((m) => (
                    <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                      <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                      <td className="px-4 py-2.5 text-zinc-600">{m.full_name || "\u2014"}</td>
                      <td className="px-4 py-2.5 text-zinc-600">
                        <span className="flex items-center gap-1.5">
                          {m.role}
                          {m.is_admin && (
                            <span className="bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded text-xs font-medium">
                              Admin
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-zinc-500 text-xs">
                        {m.permissions.length > 0 ? `${m.permissions.length} perms` : "\u2014"}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                          {m.status}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        {m.role !== "Company Admin" && m.is_active && (
                          <button onClick={() => handleDeactivate(m.id)} className="text-xs text-red-600 hover:underline">
                            Deactivate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {invites.length > 0 && (
            <>
              <h2 className="text-sm font-medium text-zinc-900 mb-3">Pending Invites ({invites.length})</h2>
              <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-zinc-50 border-b border-zinc-200">
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Permissions</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invites.map((m) => (
                      <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                        <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                        <td className="px-4 py-2.5 text-zinc-600">
                          <span className="flex items-center gap-1.5">
                            {m.role}
                            {m.is_admin && (
                              <span className="bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded text-xs font-medium">
                                Admin
                              </span>
                            )}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-zinc-500 text-xs">
                          {m.permissions.length > 0 ? `${m.permissions.length} perms` : "\u2014"}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                            {m.status}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 space-x-3">
                          <button onClick={() => handleResend(m.id)} className="text-xs text-blue-600 hover:underline">
                            Resend
                          </button>
                          <button onClick={() => handleRevoke(m.id)} className="text-xs text-red-600 hover:underline">
                            Revoke
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
