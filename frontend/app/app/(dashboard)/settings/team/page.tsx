"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface TeamMemberAssignment {
  org_unit_id: string;
  org_unit_name: string;
  role_name: string;
}

interface TeamMember {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_super_admin: boolean;
  assignments: TeamMemberAssignment[];
  source: "user" | "invite";
  status: string;
  created_at: string;
}

interface MeData {
  is_super_admin: boolean;
}

/* ─── Icons ─── */

function IconUsers({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
    </svg>
  );
}

function IconMail({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
    </svg>
  );
}

/* ─── Skeleton ─── */

function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr className="border-b border-zinc-100">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 bg-zinc-100 rounded animate-pulse" style={{ width: i === 0 ? "60%" : "40%" }} />
        </td>
      ))}
    </tr>
  );
}

function TableSkeleton({ cols, rows = 3 }: { cols: number; rows?: number }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-zinc-50 border-b border-zinc-200">
            {Array.from({ length: cols }).map((_, i) => (
              <th key={i} className="px-4 py-2.5">
                <div className="h-3 bg-zinc-200 rounded animate-pulse w-16" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, i) => (
            <SkeletonRow key={i} cols={cols} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─── Confirmation dialog ─── */

interface ConfirmAction {
  message: string;
  onConfirm: () => void;
}

function ConfirmDialog({
  action,
  onClose,
}: {
  action: ConfirmAction;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl p-6 max-w-sm w-full mx-4 shadow-lg">
        <p className="text-sm text-zinc-700 mb-4">{action.message}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-zinc-600 hover:text-zinc-900 rounded-lg cursor-pointer transition-colors duration-150"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              action.onConfirm();
              onClose();
            }}
            className="px-3 py-1.5 text-sm text-white bg-red-600 hover:bg-red-700 rounded-lg cursor-pointer transition-colors duration-150"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Page ─── */

export default function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [me, setMe] = useState<MeData | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);

  // Invite form — email only
  const [inviteEmail, setInviteEmail] = useState("");
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

  async function loadData() {
    try {
      const token = await getToken();
      if (!token) return;
      const [memberData, meData] = await Promise.all([
        apiFetch<TeamMember[]>("/api/settings/team/members", { token }),
        apiFetch<MeData>("/api/auth/me", { token }),
      ]);
      setMembers(memberData);
      setMe(meData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load team");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadData(); }, []);

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
        body: JSON.stringify({ email: inviteEmail }),
      });
      setInviteEmail("");
      setInviteSuccess(result.invite_url
        ? `Invite sent! URL: ${result.invite_url}`
        : "Invite sent!");
      await loadData();
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
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend");
    }
  }

  async function handleRevoke(inviteId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/revoke/" + inviteId, { method: "POST", token });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  }

  async function handleDeactivate(userId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/deactivate/" + userId, { method: "POST", token });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate");
    }
  }

  const isSuperAdmin = me?.is_super_admin ?? false;
  const users = members.filter((m) => m.source === "user");
  const invites = members.filter((m) => m.source === "invite");

  const statusColor: Record<string, string> = {
    active: "bg-green-50 text-green-700",
    inactive: "bg-zinc-100 text-zinc-500",
    pending: "bg-amber-50 text-amber-700",
  };

  return (
    <>
      {/* Confirmation dialog */}
      {confirmAction && (
        <ConfirmDialog
          action={confirmAction}
          onClose={() => setConfirmAction(null)}
        />
      )}

      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Team Management</h1>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
      )}
      {inviteSuccess && (
        <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg p-3 mb-4">
          {inviteSuccess}
        </div>
      )}

      {/* Invite form — only visible to Super Admin, email only */}
      {isSuperAdmin && (
        <form onSubmit={handleInvite} className="bg-white border border-zinc-200 rounded-lg p-5 mb-6">
          <h2 className="text-sm font-medium text-zinc-900 mb-3">Invite Team Member</h2>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
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
            <button
              type="submit"
              disabled={inviteLoading}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
            >
              {inviteLoading ? "Sending..." : "Send Invite"}
            </button>
          </div>
          <p className="text-xs text-zinc-400 mt-2">
            Roles and org unit assignments can be configured after the user joins.
          </p>
        </form>
      )}

      {loading ? (
        <>
          <div className="h-4 w-28 bg-zinc-100 rounded animate-pulse mb-3" />
          <TableSkeleton cols={isSuperAdmin ? 5 : 4} rows={3} />
          <div className="h-4 w-36 bg-zinc-100 rounded animate-pulse mb-3 mt-6" />
          <TableSkeleton cols={isSuperAdmin ? 3 : 2} rows={2} />
        </>
      ) : (
        <>
          <h2 className="text-sm font-medium text-zinc-900 mb-3">Members ({users.length})</h2>
          {users.length === 0 ? (
            <div className="flex flex-col items-center justify-center bg-white border border-dashed border-zinc-200 rounded-xl py-10 mb-6 text-center">
              <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mb-3">
                <IconUsers className="w-5 h-5 text-zinc-400" />
              </div>
              <p className="text-sm font-medium text-zinc-600 mb-1">No team members yet</p>
              <p className="text-xs text-zinc-400">Invite a colleague to get started.</p>
            </div>
          ) : (
            <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-zinc-50 border-b border-zinc-200">
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Name</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                      {isSuperAdmin && (
                        <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((m) => (
                      <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                        <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                        <td className="px-4 py-2.5 text-zinc-600">{m.full_name || "—"}</td>
                        <td className="px-4 py-2.5 text-zinc-600">
                          {m.is_super_admin ? (
                            <span className="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded text-xs font-medium">
                              Super Admin
                            </span>
                          ) : m.assignments.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {m.assignments.map((a) => (
                                <span
                                  key={`${a.org_unit_id}-${a.role_name}`}
                                  className="bg-zinc-100 text-zinc-600 px-1.5 py-0.5 rounded text-xs"
                                  title={a.org_unit_name}
                                >
                                  {a.role_name}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="text-zinc-400 italic">Unassigned</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                            {m.status}
                          </span>
                        </td>
                        {isSuperAdmin && (
                          <td className="px-4 py-2.5">
                            {!m.is_super_admin && m.is_active && (
                              <button
                                onClick={() =>
                                  setConfirmAction({
                                    message: `Deactivate ${m.email}? They will lose access to ProjectX.`,
                                    onConfirm: () => handleDeactivate(m.id),
                                  })
                                }
                                className="text-xs text-red-600 hover:text-red-700 hover:underline cursor-pointer transition-colors duration-150"
                              >
                                Deactivate
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {invites.length > 0 ? (
            <>
              <h2 className="text-sm font-medium text-zinc-900 mb-3">Pending Invites ({invites.length})</h2>
              <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-zinc-50 border-b border-zinc-200">
                        <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                        <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                        {isSuperAdmin && (
                          <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {invites.map((m) => (
                        <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                          <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                          <td className="px-4 py-2.5">
                            <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                              {m.status}
                            </span>
                          </td>
                          {isSuperAdmin && (
                            <td className="px-4 py-2.5">
                              <div className="flex items-center gap-2">
                                <button
                                  onClick={() => handleResend(m.id)}
                                  className="text-xs text-blue-600 hover:text-blue-700 hover:underline cursor-pointer transition-colors duration-150"
                                >
                                  Resend
                                </button>
                                <span className="text-zinc-300">·</span>
                                <button
                                  onClick={() =>
                                    setConfirmAction({
                                      message: `Revoke the invite for ${m.email}? This cannot be undone.`,
                                      onConfirm: () => handleRevoke(m.id),
                                    })
                                  }
                                  className="text-xs text-red-600 hover:text-red-700 hover:underline cursor-pointer transition-colors duration-150"
                                >
                                  Revoke
                                </button>
                              </div>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center bg-white border border-dashed border-zinc-200 rounded-xl py-8 text-center">
              <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mb-3">
                <IconMail className="w-5 h-5 text-zinc-400" />
              </div>
              <p className="text-sm font-medium text-zinc-600 mb-1">No pending invites</p>
              <p className="text-xs text-zinc-400">Invites you send will appear here until accepted.</p>
            </div>
          )}
        </>
      )}
    </>
  );
}
