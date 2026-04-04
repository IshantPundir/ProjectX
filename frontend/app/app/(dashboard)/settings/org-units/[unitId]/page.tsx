"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

/* ─── Types (match backend schemas exactly) ─── */

interface OrgUnit {
  id: string;
  client_id: string;
  parent_unit_id: string | null;
  name: string;
  unit_type: string;
  member_count: number;
  created_at: string;
}

interface MemberRole {
  role_id: string;
  role_name: string;
  assigned_at: string;
}

interface OrgUnitMember {
  user_id: string;
  email: string;
  full_name: string | null;
  roles: MemberRole[];
}

interface AvailableRole {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  is_system: boolean;
}

interface TeamMember {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_super_admin: boolean;
  source: string;
  status: string;
  assignments: { org_unit_id: string; org_unit_name: string; role_name: string }[];
  created_at: string;
}

interface MeData {
  is_super_admin: boolean;
  assignments: {
    org_unit_id: string;
    org_unit_name: string;
    role_name: string;
    permissions: string[];
  }[];
}

/* ─── Constants ─── */

const UNIT_TYPES = [
  { value: "department", label: "Department" },
  { value: "team", label: "Team" },
  { value: "branch", label: "Branch" },
  { value: "region", label: "Region" },
  { value: "client_account", label: "Client Account" },
] as const;

const TYPE_LABELS: Record<string, string> = {
  client_account: "Client Account",
  department: "Department",
  team: "Team",
  branch: "Branch",
  region: "Region",
};

/* ─── Icons ─── */

function IconArrowLeft({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
    </svg>
  );
}

function IconPlus({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
    </svg>
  );
}

function IconPencil({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z" />
    </svg>
  );
}

function IconUsers({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
    </svg>
  );
}

function IconX({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function IconChevronRight({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
    </svg>
  );
}

/* ─── Helpers ─── */

function buildAncestry(units: OrgUnit[], targetId: string): OrgUnit[] {
  const unitMap = new Map(units.map((u) => [u.id, u]));
  const chain: OrgUnit[] = [];
  let current = unitMap.get(targetId);
  while (current) {
    chain.unshift(current);
    current = current.parent_unit_id ? unitMap.get(current.parent_unit_id) : undefined;
  }
  return chain;
}

/* ─── Page ─── */

export default function OrgUnitDetailPage() {
  const params = useParams();
  const router = useRouter();
  const unitId = params.unitId as string;

  const [allUnits, setAllUnits] = useState<OrgUnit[]>([]);
  const [me, setMe] = useState<MeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Members
  const [members, setMembers] = useState<OrgUnitMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(true);
  const [tenantUsers, setTenantUsers] = useState<TeamMember[]>([]);
  const [availableRoles, setAvailableRoles] = useState<AvailableRole[]>([]);

  // Edit unit
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("");
  const [saving, setSaving] = useState(false);

  // Add member
  const [showAddMember, setShowAddMember] = useState(false);
  const [userSearch, setUserSearch] = useState("");
  const [addUserId, setAddUserId] = useState("");
  const [addUserEmail, setAddUserEmail] = useState("");
  const [addRoleId, setAddRoleId] = useState("");
  const [addingMember, setAddingMember] = useState(false);

  // Confirm
  const [confirmAction, setConfirmAction] = useState<{
    message: string;
    onConfirm: () => void;
  } | null>(null);

  const getToken = useCallback(async () => {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      window.location.href = "/login";
      return null;
    }
    return session.access_token;
  }, []);

  /* ─── Data loading ─── */

  const loadAll = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) return;
      const [unitsData, meData] = await Promise.all([
        apiFetch<OrgUnit[]>("/api/org-units", { token }),
        apiFetch<MeData>("/api/auth/me", { token }),
      ]);
      setAllUnits(unitsData);
      setMe(meData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  const loadMembers = useCallback(async () => {
    setMembersLoading(true);
    try {
      const token = await getToken();
      if (!token) return;
      const [m, u, r] = await Promise.all([
        apiFetch<OrgUnitMember[]>(`/api/org-units/${unitId}/members`, { token }),
        apiFetch<TeamMember[]>("/api/settings/team/members", { token }),
        apiFetch<AvailableRole[]>("/api/roles", { token }),
      ]);
      setMembers(m);
      setTenantUsers(u.filter((x) => x.source === "user"));
      setAvailableRoles(r);
    } catch {
      // Members load error is non-fatal — unit info still shows
    } finally {
      setMembersLoading(false);
    }
  }, [getToken, unitId]);

  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => { loadMembers(); }, [loadMembers]);

  /* ─── Derived ─── */

  const unit = allUnits.find((u) => u.id === unitId);
  const ancestry = useMemo(
    () => (unit ? buildAncestry(allUnits, unitId) : []),
    [allUnits, unitId, unit],
  );
  const childUnits = useMemo(
    () => allUnits.filter((u) => u.parent_unit_id === unitId),
    [allUnits, unitId],
  );

  const canManage = useMemo(() => {
    if (!me) return false;
    if (me.is_super_admin) return true;
    return me.assignments.some((a) => a.org_unit_id === unitId && a.role_name === "Admin");
  }, [me, unitId]);

  const memberUserIds = useMemo(() => new Set(members.map((m) => m.user_id)), [members]);

  const filteredUsers = useMemo(() => {
    const eligible = tenantUsers.filter((u) => !memberUserIds.has(u.id));
    if (!userSearch.trim()) return eligible.slice(0, 6);
    const q = userSearch.toLowerCase();
    return eligible.filter(
      (u) => u.email.toLowerCase().includes(q) || (u.full_name || "").toLowerCase().includes(q),
    );
  }, [tenantUsers, memberUserIds, userSearch]);

  /* ─── Actions ─── */

  async function handleSaveEdit() {
    if (!unit) return;
    setError("");
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({ name: editName.trim(), unit_type: editType }),
      });
      setEditing(false);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSaving(false);
    }
  }

  async function handleAddMember() {
    if (!addUserId || !addRoleId) return;
    setError("");
    setAddingMember(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}/members`, {
        method: "POST",
        token,
        body: JSON.stringify({ user_id: addUserId, role_id: addRoleId }),
      });
      setAddUserId("");
      setAddUserEmail("");
      setUserSearch("");
      await Promise.all([loadMembers(), loadAll()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign role");
    } finally {
      setAddingMember(false);
    }
  }

  async function handleRemoveRole(userId: string, roleId: string) {
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}/members/${userId}/roles/${roleId}`, {
        method: "DELETE",
        token,
      });
      await Promise.all([loadMembers(), loadAll()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove role");
    }
  }

  async function handleRemoveMember(userId: string) {
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}/members/${userId}`, {
        method: "DELETE",
        token,
      });
      await Promise.all([loadMembers(), loadAll()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove member");
    }
  }

  /* ─── Loading state ─── */

  if (loading) {
    return (
      <div className="max-w-4xl space-y-4">
        <div className="h-5 w-48 bg-zinc-100 rounded animate-pulse" />
        <div className="h-8 w-64 bg-zinc-100 rounded animate-pulse" />
        <div className="h-40 bg-zinc-100 rounded-xl animate-pulse" />
      </div>
    );
  }

  if (!unit) {
    return (
      <div className="max-w-4xl">
        <p className="text-sm text-zinc-500">Unit not found.</p>
        <button
          onClick={() => router.push("/settings/org-units")}
          className="mt-2 text-sm text-green-600 hover:text-green-700 cursor-pointer"
        >
          Back to Org Units
        </button>
      </div>
    );
  }

  const isNested = ancestry.length > 1;

  return (
    <>
      {/* Confirmation dialog */}
      {confirmAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl p-6 max-w-sm w-full mx-4 shadow-lg">
            <p className="text-sm text-zinc-700 mb-4">{confirmAction.message}</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmAction(null)}
                className="px-3 py-1.5 text-sm text-zinc-600 hover:text-zinc-900 rounded-lg cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={() => { confirmAction.onConfirm(); setConfirmAction(null); }}
                className="px-3 py-1.5 text-sm text-white bg-red-600 hover:bg-red-700 rounded-lg cursor-pointer transition-colors duration-150"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="max-w-4xl">
        {/* Back link */}
        <button
          onClick={() => router.push("/settings/org-units")}
          className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-700 mb-4 cursor-pointer transition-colors duration-100"
        >
          <IconArrowLeft className="w-3.5 h-3.5" />
          All Units
        </button>

        {/* ─── Nesting Map (breadcrumb for nested units) ─── */}
        {isNested && (
          <nav aria-label="Unit hierarchy" className="mb-5">
            <div className="bg-zinc-50 border border-zinc-200 rounded-lg px-4 py-3">
              <p className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider mb-2">
                Hierarchy
              </p>
              <div className="flex items-center gap-1 flex-wrap">
                {ancestry.map((ancestor, i) => {
                  const isLast = i === ancestry.length - 1;
                  return (
                    <span key={ancestor.id} className="flex items-center gap-1">
                      {isLast ? (
                        <span className="text-sm font-semibold text-zinc-900">
                          {ancestor.name}
                        </span>
                      ) : (
                        <button
                          onClick={() => router.push(`/settings/org-units/${ancestor.id}`)}
                          className="text-sm text-green-600 hover:text-green-700 hover:underline cursor-pointer"
                        >
                          {ancestor.name}
                        </button>
                      )}
                      {!isLast && (
                        <IconChevronRight className="w-3 h-3 text-zinc-300 shrink-0" />
                      )}
                    </span>
                  );
                })}
              </div>
            </div>
          </nav>
        )}

        {/* Error */}
        {error && (
          <div
            role="alert"
            className="flex items-start justify-between text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4"
          >
            <span>{error}</span>
            <button onClick={() => setError("")} className="ml-2 shrink-0 cursor-pointer" aria-label="Dismiss">
              <IconX className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ─── Unit Header ─── */}
        <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
          {editing ? (
            <div className="space-y-3">
              <h2 className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Edit Unit</h2>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="edit-name" className="block text-xs font-medium text-zinc-600 mb-1">Name</label>
                  <input
                    id="edit-name"
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveEdit();
                      if (e.key === "Escape") setEditing(false);
                    }}
                  />
                </div>
                <div>
                  <label htmlFor="edit-type" className="block text-xs font-medium text-zinc-600 mb-1">Type</label>
                  <select
                    id="edit-type"
                    value={editType}
                    onChange={(e) => setEditType(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white cursor-pointer"
                  >
                    {UNIT_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleSaveEdit}
                  disabled={saving || !editName.trim()}
                  className="bg-green-600 text-white px-3.5 py-1.5 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
                >
                  {saving ? "Saving..." : "Save Changes"}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="text-sm text-zinc-500 hover:text-zinc-700 px-3 py-1.5 cursor-pointer"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-start justify-between">
              <div>
                <h1 className="text-xl font-semibold text-zinc-900">{unit.name}</h1>
                <div className="flex items-center gap-3 mt-1.5">
                  <span className="bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded text-xs font-medium">
                    {TYPE_LABELS[unit.unit_type] || unit.unit_type}
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                    <IconUsers className="w-3 h-3" />
                    {members.length} member{members.length !== 1 ? "s" : ""}
                  </span>
                  <span className="text-xs text-zinc-400">
                    Created {new Date(unit.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
              {canManage && (
                <button
                  onClick={() => { setEditName(unit.name); setEditType(unit.unit_type); setEditing(true); }}
                  className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-700 border border-zinc-200 rounded-lg px-3 py-1.5 cursor-pointer transition-colors duration-150"
                >
                  <IconPencil />
                  Edit
                </button>
              )}
            </div>
          )}
        </div>

        {/* ─── Sub-units ─── */}
        {childUnits.length > 0 && (
          <div className="mb-6">
            <h2 className="text-sm font-semibold text-zinc-900 mb-3">
              Sub-units
              <span className="text-zinc-400 font-normal ml-1">({childUnits.length})</span>
            </h2>
            <div className="bg-white border border-zinc-200 rounded-xl divide-y divide-zinc-100">
              {childUnits.map((child) => (
                <button
                  key={child.id}
                  type="button"
                  onClick={() => router.push(`/settings/org-units/${child.id}`)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-zinc-50 cursor-pointer transition-colors duration-100 text-left group first:rounded-t-xl last:rounded-b-xl"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="text-sm font-medium text-zinc-900">{child.name}</span>
                    <span className="bg-zinc-100 text-zinc-500 px-1.5 py-0.5 rounded text-[10px] font-medium">
                      {TYPE_LABELS[child.unit_type] || child.unit_type}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                      <IconUsers className="w-3 h-3" />
                      {child.member_count}
                    </span>
                    <IconChevronRight className="w-3.5 h-3.5 text-zinc-300 group-hover:text-zinc-500 transition-colors duration-100" />
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ─── Members ─── */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-zinc-900">
              Members
              <span className="text-zinc-400 font-normal ml-1">({members.length})</span>
            </h2>
            {canManage && !showAddMember && (
              <button
                onClick={() => setShowAddMember(true)}
                className="inline-flex items-center gap-1.5 text-sm text-green-600 hover:text-green-700 font-medium cursor-pointer"
              >
                <IconPlus className="w-3.5 h-3.5" />
                Add Member
              </button>
            )}
          </div>

          {/* Add member form */}
          {canManage && showAddMember && (
            <div className="bg-white border border-zinc-200 rounded-xl p-5 mb-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold text-zinc-700">Add member to {unit.name}</h3>
                <button
                  onClick={() => { setShowAddMember(false); setAddUserId(""); setAddUserEmail(""); setUserSearch(""); }}
                  className="text-zinc-400 hover:text-zinc-600 cursor-pointer"
                  aria-label="Close"
                >
                  <IconX className="w-3.5 h-3.5" />
                </button>
              </div>

              <div className="grid grid-cols-2 gap-4">
                {/* User picker */}
                <div>
                  <label htmlFor="user-search" className="block text-xs font-medium text-zinc-600 mb-1">User</label>
                  {addUserId ? (
                    <div className="flex items-center justify-between border border-green-200 bg-green-50 rounded-lg px-3 py-2">
                      <span className="text-sm text-zinc-900 truncate">{addUserEmail}</span>
                      <button
                        onClick={() => { setAddUserId(""); setAddUserEmail(""); setUserSearch(""); }}
                        className="text-zinc-400 hover:text-zinc-600 cursor-pointer shrink-0 ml-2"
                        aria-label="Clear selection"
                      >
                        <IconX className="w-3 h-3" />
                      </button>
                    </div>
                  ) : (
                    <div className="relative">
                      <input
                        id="user-search"
                        type="text"
                        value={userSearch}
                        onChange={(e) => setUserSearch(e.target.value)}
                        className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
                        placeholder="Search by email or name..."
                      />
                      {filteredUsers.length > 0 && (
                        <div className="absolute left-0 right-0 mt-1 border border-zinc-200 rounded-lg bg-white max-h-48 overflow-y-auto shadow-lg z-10">
                          {filteredUsers.map((u) => (
                            <button
                              key={u.id}
                              type="button"
                              onClick={() => { setAddUserId(u.id); setAddUserEmail(u.email); setUserSearch(""); }}
                              className="w-full text-left px-3 py-2.5 hover:bg-zinc-50 border-b border-zinc-50 last:border-0 cursor-pointer transition-colors duration-100"
                            >
                              <p className="text-sm text-zinc-900">{u.email}</p>
                              {u.full_name && <p className="text-xs text-zinc-500">{u.full_name}</p>}
                            </button>
                          ))}
                        </div>
                      )}
                      {userSearch && filteredUsers.length === 0 && (
                        <p className="text-xs text-zinc-400 mt-1.5">No matching users found</p>
                      )}
                    </div>
                  )}
                </div>

                {/* Role picker */}
                <div>
                  <label htmlFor="add-role" className="block text-xs font-medium text-zinc-600 mb-1">Role</label>
                  <select
                    id="add-role"
                    value={addRoleId}
                    onChange={(e) => setAddRoleId(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                  >
                    <option value="">Select a role...</option>
                    {availableRoles.map((r) => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleAddMember}
                  disabled={!addUserId || !addRoleId || addingMember}
                  className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-40 cursor-pointer transition-colors duration-150"
                >
                  {addingMember ? "Adding..." : "Add to Unit"}
                </button>
              </div>
            </div>
          )}

          {/* Member list */}
          {membersLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-20 bg-zinc-100 rounded-xl animate-pulse" />
              ))}
            </div>
          ) : members.length === 0 ? (
            <div className="text-center py-12 bg-white border border-dashed border-zinc-200 rounded-xl">
              <IconUsers className="w-7 h-7 text-zinc-300 mx-auto mb-2" />
              <p className="text-sm text-zinc-500">No members in this unit yet</p>
              {canManage && (
                <button
                  onClick={() => setShowAddMember(true)}
                  className="mt-2 inline-flex items-center gap-1 text-sm text-green-600 hover:text-green-700 font-medium cursor-pointer"
                >
                  <IconPlus className="w-3.5 h-3.5" />
                  Add your first member
                </button>
              )}
            </div>
          ) : (
            <div className="bg-white border border-zinc-200 rounded-xl divide-y divide-zinc-100">
              {members.map((m) => (
                <div key={m.user_id} className="flex items-start justify-between px-5 py-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-zinc-900">
                      {m.full_name || m.email}
                    </p>
                    {m.full_name && (
                      <p className="text-xs text-zinc-400 mt-0.5">{m.email}</p>
                    )}
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {m.roles.map((r) => (
                        <span
                          key={r.role_id}
                          className="inline-flex items-center gap-1 bg-zinc-100 text-zinc-700 px-2 py-0.5 rounded text-xs"
                        >
                          {r.role_name}
                          {canManage && m.roles.length > 1 && (
                            <button
                              type="button"
                              onClick={() =>
                                setConfirmAction({
                                  message: `Remove the "${r.role_name}" role from ${m.email}?`,
                                  onConfirm: () => handleRemoveRole(m.user_id, r.role_id),
                                })
                              }
                              className="text-zinc-400 hover:text-red-500 cursor-pointer leading-none"
                              aria-label={`Remove ${r.role_name} role`}
                            >
                              &times;
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  </div>
                  {canManage && (
                    <button
                      type="button"
                      onClick={() =>
                        setConfirmAction({
                          message: `Remove ${m.email} from ${unit.name}? All their roles in this unit will be removed.`,
                          onConfirm: () => handleRemoveMember(m.user_id),
                        })
                      }
                      className="text-xs text-red-500 hover:text-red-600 shrink-0 cursor-pointer ml-4 mt-0.5"
                    >
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
