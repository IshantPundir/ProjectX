"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
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

/* ─── SVG Icons (inline, no dependencies) ─── */

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

function IconChevron({ className = "w-3 h-3" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
    </svg>
  );
}

function IconBuilding({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" />
    </svg>
  );
}

/* ─── Helpers ─── */

function buildTree(units: OrgUnit[]): { unit: OrgUnit; depth: number }[] {
  const childrenMap = new Map<string | null, OrgUnit[]>();
  for (const u of units) {
    const key = u.parent_unit_id;
    childrenMap.set(key, [...(childrenMap.get(key) || []), u]);
  }
  const result: { unit: OrgUnit; depth: number }[] = [];
  function walk(parentId: string | null, depth: number) {
    for (const child of childrenMap.get(parentId) || []) {
      result.push({ unit: child, depth });
      walk(child.id, depth + 1);
    }
  }
  walk(null, 0);
  return result;
}

/* ─── Page ─── */

export default function OrgUnitsPage() {
  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [me, setMe] = useState<MeData | null>(null);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createType, setCreateType] = useState("department");
  const [createParent, setCreateParent] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit inline
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("");
  const [saving, setSaving] = useState(false);

  // Detail panel
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [members, setMembers] = useState<OrgUnitMember[]>([]);
  const [tenantUsers, setTenantUsers] = useState<TeamMember[]>([]);
  const [availableRoles, setAvailableRoles] = useState<AvailableRole[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  // Add member
  const [showAddMember, setShowAddMember] = useState(false);
  const [userSearch, setUserSearch] = useState("");
  const [addUserId, setAddUserId] = useState("");
  const [addUserEmail, setAddUserEmail] = useState("");
  const [addRoleId, setAddRoleId] = useState("");
  const [addingMember, setAddingMember] = useState(false);

  // Confirmation
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

  const clearError = useCallback(() => setError(""), []);

  /* ─── Data loading ─── */

  const loadUnits = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) return;
      const [unitsData, meData] = await Promise.all([
        apiFetch<OrgUnit[]>("/api/org-units", { token }),
        apiFetch<MeData>("/api/auth/me", { token }),
      ]);
      setUnits(unitsData);
      setMe(meData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  const loadMembers = useCallback(async (unitId: string) => {
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    } finally {
      setMembersLoading(false);
    }
  }, [getToken]);

  useEffect(() => { loadUnits(); }, [loadUnits]);
  useEffect(() => {
    if (selectedId) {
      loadMembers(selectedId);
      setShowAddMember(false);
      setAddUserId("");
      setAddUserEmail("");
      setUserSearch("");
    }
  }, [selectedId, loadMembers]);

  /* ─── Authorization ─── */

  function canManageUnit(unitId: string | null): boolean {
    if (!me) return false;
    if (me.is_super_admin) return true;
    if (!unitId) return false;
    return me.assignments.some(
      (a) => a.org_unit_id === unitId && a.role_name === "Admin",
    );
  }

  /* ─── Actions ─── */

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    clearError();
    try {
      const token = await getToken();
      if (!token) return;
      const newUnit = await apiFetch<OrgUnit>("/api/org-units", {
        method: "POST",
        token,
        body: JSON.stringify({
          name: createName.trim(),
          unit_type: createType,
          parent_unit_id: createParent || null,
        }),
      });
      setCreateName("");
      setCreateType("department");
      setCreateParent("");
      setShowCreate(false);
      await loadUnits();
      setSelectedId(newUnit.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create unit");
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveEdit(unitId: string) {
    clearError();
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({ name: editName.trim(), unit_type: editType }),
      });
      setEditId(null);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSaving(false);
    }
  }

  async function handleAddMember() {
    if (!selectedId || !addUserId || !addRoleId) return;
    clearError();
    setAddingMember(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedId}/members`, {
        method: "POST",
        token,
        body: JSON.stringify({ user_id: addUserId, role_id: addRoleId }),
      });
      setAddUserId("");
      setAddUserEmail("");
      setUserSearch("");
      await loadMembers(selectedId);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign role");
    } finally {
      setAddingMember(false);
    }
  }

  async function handleRemoveRole(userId: string, roleId: string) {
    if (!selectedId) return;
    clearError();
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedId}/members/${userId}/roles/${roleId}`, {
        method: "DELETE",
        token,
      });
      await loadMembers(selectedId);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove role");
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!selectedId) return;
    clearError();
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedId}/members/${userId}`, {
        method: "DELETE",
        token,
      });
      await loadMembers(selectedId);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove member");
    }
  }

  /* ─── Derived state ─── */

  const tree = useMemo(() => buildTree(units), [units]);
  const selectedUnit = units.find((u) => u.id === selectedId);
  const canManage = canManageUnit(selectedId);

  const memberUserIds = useMemo(
    () => new Set(members.map((m) => m.user_id)),
    [members],
  );

  const filteredUsers = useMemo(() => {
    const eligible = tenantUsers.filter((u) => !memberUserIds.has(u.id));
    if (!userSearch.trim()) return eligible.slice(0, 6);
    const q = userSearch.toLowerCase();
    return eligible.filter(
      (u) =>
        u.email.toLowerCase().includes(q) ||
        (u.full_name || "").toLowerCase().includes(q),
    );
  }, [tenantUsers, memberUserIds, userSearch]);

  /* ─── Render ─── */

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
                onClick={() => {
                  confirmAction.onConfirm();
                  setConfirmAction(null);
                }}
                className="px-3 py-1.5 text-sm text-white bg-red-600 hover:bg-red-700 rounded-lg cursor-pointer transition-colors duration-150"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex gap-6 h-full">
        {/* ─── Left: Unit list ─── */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-5">
            <div>
              <h1 className="text-lg font-semibold text-zinc-900">Organizational Units</h1>
              <p className="text-xs text-zinc-400 mt-0.5">
                {units.length} unit{units.length !== 1 ? "s" : ""}
              </p>
            </div>
            {me?.is_super_admin && (
              <button
                onClick={() => setShowCreate(!showCreate)}
                className="inline-flex items-center gap-1.5 bg-green-600 text-white px-3.5 py-2 rounded-lg text-sm font-medium hover:bg-green-700 cursor-pointer transition-colors duration-150"
              >
                {showCreate ? (
                  <>
                    <IconX className="w-3.5 h-3.5" />
                    Cancel
                  </>
                ) : (
                  <>
                    <IconPlus className="w-3.5 h-3.5" />
                    New Unit
                  </>
                )}
              </button>
            )}
          </div>

          {/* Error banner */}
          {error && (
            <div
              role="alert"
              className="flex items-start justify-between text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4"
            >
              <span>{error}</span>
              <button onClick={clearError} className="ml-2 shrink-0 cursor-pointer" aria-label="Dismiss error">
                <IconX className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {/* Create form */}
          {showCreate && me?.is_super_admin && (
            <form onSubmit={handleCreate} className="bg-white border border-zinc-200 rounded-xl p-5 mb-5 space-y-4">
              <h2 className="text-sm font-semibold text-zinc-900">Create Organizational Unit</h2>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="create-name" className="block text-xs font-medium text-zinc-600 mb-1">
                    Name
                  </label>
                  <input
                    id="create-name"
                    type="text"
                    required
                    value={createName}
                    onChange={(e) => setCreateName(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
                    placeholder="e.g., Engineering"
                  />
                </div>
                <div>
                  <label htmlFor="create-type" className="block text-xs font-medium text-zinc-600 mb-1">
                    Type
                  </label>
                  <select
                    id="create-type"
                    value={createType}
                    onChange={(e) => setCreateType(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                  >
                    {UNIT_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {units.length > 0 && (
                <div>
                  <label htmlFor="create-parent" className="block text-xs font-medium text-zinc-600 mb-1">
                    Parent Unit
                    <span className="font-normal text-zinc-400 ml-1">(optional)</span>
                  </label>
                  <select
                    id="create-parent"
                    value={createParent}
                    onChange={(e) => setCreateParent(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                  >
                    <option value="">None (top-level)</option>
                    {tree.map(({ unit: u, depth }) => (
                      <option key={u.id} value={u.id}>
                        {"\u00A0\u00A0".repeat(depth)}
                        {u.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={creating || !createName.trim()}
                  className="inline-flex items-center gap-1.5 bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
                >
                  {creating ? "Creating..." : "Create Unit"}
                </button>
              </div>
            </form>
          )}

          {/* Unit list */}
          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 bg-zinc-100 rounded-lg animate-pulse" />
              ))}
            </div>
          ) : units.length === 0 ? (
            <div className="text-center py-20 border border-dashed border-zinc-200 rounded-xl">
              <IconBuilding className="w-8 h-8 text-zinc-300 mx-auto mb-3" />
              <p className="text-sm text-zinc-500 mb-1">No organizational units yet</p>
              <p className="text-xs text-zinc-400 mb-4">
                Create your first unit to start assigning team members to roles.
              </p>
              {me?.is_super_admin && (
                <button
                  onClick={() => setShowCreate(true)}
                  className="inline-flex items-center gap-1 text-sm text-green-600 hover:text-green-700 font-medium cursor-pointer"
                >
                  <IconPlus className="w-3.5 h-3.5" />
                  Create your first unit
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-0.5">
              {tree.map(({ unit: u, depth }) => {
                const isSelected = selectedId === u.id;
                const isEditing = editId === u.id;

                return (
                  <div key={u.id}>
                    {isEditing ? (
                      /* ─── Inline edit row ─── */
                      <div
                        className="flex items-center gap-2 py-2.5 px-3 rounded-lg border border-green-200 bg-green-50"
                        style={{ paddingLeft: `${depth * 20 + 12}px` }}
                      >
                        <input
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          className="border border-zinc-300 rounded-lg px-2.5 py-1.5 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-green-600"
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleSaveEdit(u.id);
                            if (e.key === "Escape") setEditId(null);
                          }}
                        />
                        <select
                          value={editType}
                          onChange={(e) => setEditType(e.target.value)}
                          className="border border-zinc-300 rounded-lg px-2 py-1.5 text-xs bg-white cursor-pointer"
                        >
                          {UNIT_TYPES.map((t) => (
                            <option key={t.value} value={t.value}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleSaveEdit(u.id)}
                          disabled={saving || !editName.trim()}
                          className="text-xs text-green-600 hover:text-green-700 font-medium cursor-pointer disabled:opacity-50"
                        >
                          {saving ? "Saving..." : "Save"}
                        </button>
                        <button
                          onClick={() => setEditId(null)}
                          className="text-xs text-zinc-400 hover:text-zinc-600 cursor-pointer"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      /* ─── Normal row ─── */
                      <button
                        type="button"
                        onClick={() => setSelectedId(isSelected ? null : u.id)}
                        className={`w-full flex items-center justify-between py-2.5 pr-3 rounded-lg cursor-pointer transition-colors duration-150 text-left group ${
                          isSelected
                            ? "bg-green-50 border border-green-200"
                            : "hover:bg-zinc-50 border border-transparent"
                        }`}
                        style={{ paddingLeft: `${depth * 20 + 12}px` }}
                        aria-pressed={isSelected}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          {depth > 0 && (
                            <IconChevron className="w-2.5 h-2.5 text-zinc-300 shrink-0" />
                          )}
                          <span className="text-sm font-medium text-zinc-900 truncate">
                            {u.name}
                          </span>
                          <span className="bg-zinc-100 text-zinc-500 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0">
                            {TYPE_LABELS[u.unit_type] || u.unit_type}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 shrink-0">
                          <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                            <IconUsers className="w-3 h-3" />
                            {u.member_count}
                          </span>
                          {canManageUnit(u.id) && (
                            <span
                              role="button"
                              tabIndex={0}
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditId(u.id);
                                setEditName(u.name);
                                setEditType(u.unit_type);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.stopPropagation();
                                  e.preventDefault();
                                  setEditId(u.id);
                                  setEditName(u.name);
                                  setEditType(u.unit_type);
                                }
                              }}
                              className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-zinc-600 cursor-pointer transition-opacity duration-150 p-1"
                              aria-label={`Edit ${u.name}`}
                            >
                              <IconPencil />
                            </span>
                          )}
                        </div>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ─── Right: Detail panel ─── */}
        {selectedUnit && (
          <div className="w-[400px] shrink-0 bg-white border border-zinc-200 rounded-xl self-start">
            {/* Header */}
            <div className="flex items-start justify-between p-5 border-b border-zinc-100">
              <div>
                <h2 className="text-sm font-semibold text-zinc-900">{selectedUnit.name}</h2>
                <p className="text-xs text-zinc-400 mt-0.5">
                  {TYPE_LABELS[selectedUnit.unit_type] || selectedUnit.unit_type}
                  {" \u00B7 "}
                  {members.length} member{members.length !== 1 ? "s" : ""}
                </p>
              </div>
              <button
                onClick={() => setSelectedId(null)}
                className="text-zinc-400 hover:text-zinc-600 cursor-pointer p-1 -mr-1"
                aria-label="Close panel"
              >
                <IconX className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5">
              {/* Add member button / form */}
              {canManage && (
                <div className="mb-5">
                  {!showAddMember ? (
                    <button
                      onClick={() => setShowAddMember(true)}
                      className="inline-flex items-center gap-1.5 text-sm text-green-600 hover:text-green-700 font-medium cursor-pointer"
                    >
                      <IconPlus className="w-3.5 h-3.5" />
                      Add member
                    </button>
                  ) : (
                    <div className="border border-zinc-200 rounded-lg p-4 space-y-3 bg-zinc-50/50">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-semibold text-zinc-700">Add member to unit</p>
                        <button
                          onClick={() => {
                            setShowAddMember(false);
                            setAddUserId("");
                            setAddUserEmail("");
                            setUserSearch("");
                          }}
                          className="text-zinc-400 hover:text-zinc-600 cursor-pointer"
                          aria-label="Close add member form"
                        >
                          <IconX className="w-3.5 h-3.5" />
                        </button>
                      </div>

                      {/* User picker */}
                      <div>
                        <label htmlFor="user-search" className="block text-xs font-medium text-zinc-600 mb-1">
                          User
                        </label>
                        {addUserId ? (
                          <div className="flex items-center justify-between border border-green-200 bg-green-50 rounded-lg px-3 py-2">
                            <span className="text-sm text-zinc-900">{addUserEmail}</span>
                            <button
                              onClick={() => {
                                setAddUserId("");
                                setAddUserEmail("");
                                setUserSearch("");
                              }}
                              className="text-zinc-400 hover:text-zinc-600 cursor-pointer"
                              aria-label="Clear user selection"
                            >
                              <IconX className="w-3 h-3" />
                            </button>
                          </div>
                        ) : (
                          <>
                            <input
                              id="user-search"
                              type="text"
                              value={userSearch}
                              onChange={(e) => setUserSearch(e.target.value)}
                              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
                              placeholder="Search by email or name..."
                            />
                            {filteredUsers.length > 0 && (
                              <div className="mt-1 border border-zinc-200 rounded-lg bg-white max-h-40 overflow-y-auto shadow-sm">
                                {filteredUsers.map((u) => (
                                  <button
                                    key={u.id}
                                    type="button"
                                    onClick={() => {
                                      setAddUserId(u.id);
                                      setAddUserEmail(u.email);
                                      setUserSearch("");
                                    }}
                                    className="w-full text-left px-3 py-2 hover:bg-zinc-50 border-b border-zinc-50 last:border-0 cursor-pointer transition-colors duration-100"
                                  >
                                    <p className="text-sm text-zinc-900">{u.email}</p>
                                    {u.full_name && (
                                      <p className="text-xs text-zinc-500">{u.full_name}</p>
                                    )}
                                  </button>
                                ))}
                              </div>
                            )}
                            {userSearch && filteredUsers.length === 0 && (
                              <p className="text-xs text-zinc-400 mt-1.5 px-1">
                                No matching users found
                              </p>
                            )}
                          </>
                        )}
                      </div>

                      {/* Role picker */}
                      <div>
                        <label htmlFor="add-role" className="block text-xs font-medium text-zinc-600 mb-1">
                          Role
                        </label>
                        <select
                          id="add-role"
                          value={addRoleId}
                          onChange={(e) => setAddRoleId(e.target.value)}
                          className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                        >
                          <option value="">Select a role...</option>
                          {availableRoles.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.name}
                            </option>
                          ))}
                        </select>
                      </div>

                      <button
                        type="button"
                        onClick={handleAddMember}
                        disabled={!addUserId || !addRoleId || addingMember}
                        className="w-full bg-green-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-green-700 disabled:opacity-40 cursor-pointer transition-colors duration-150"
                      >
                        {addingMember ? "Adding..." : "Add to Unit"}
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* Member list */}
              {membersLoading ? (
                <div className="space-y-3">
                  {[1, 2].map((i) => (
                    <div key={i} className="h-16 bg-zinc-50 rounded-lg animate-pulse" />
                  ))}
                </div>
              ) : members.length === 0 ? (
                <div className="text-center py-10">
                  <IconUsers className="w-6 h-6 text-zinc-300 mx-auto mb-2" />
                  <p className="text-xs text-zinc-400">
                    No members in this unit yet.
                    {canManage && " Add a member above."}
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {members.map((m) => (
                    <div
                      key={m.user_id}
                      className="border border-zinc-100 rounded-lg p-3 hover:border-zinc-200 transition-colors duration-100"
                    >
                      <div className="flex items-start justify-between gap-2 mb-1.5">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-zinc-900 truncate">
                            {m.full_name || m.email}
                          </p>
                          {m.full_name && (
                            <p className="text-xs text-zinc-400 truncate">{m.email}</p>
                          )}
                        </div>
                        {canManage && (
                          <button
                            type="button"
                            onClick={() =>
                              setConfirmAction({
                                message: `Remove ${m.email} from this unit? All their roles in this unit will be removed.`,
                                onConfirm: () => handleRemoveMember(m.user_id),
                              })
                            }
                            className="text-[11px] text-red-500 hover:text-red-600 shrink-0 cursor-pointer"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
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
                                aria-label={`Remove ${r.role_name} role from ${m.email}`}
                              >
                                &times;
                              </button>
                            )}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
