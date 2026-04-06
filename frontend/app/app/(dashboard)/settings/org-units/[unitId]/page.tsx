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
  created_by: string | null;
  created_by_email: string | null;
  deletable_by: string | null;
  deletable_by_email: string | null;
  admin_delete_disabled: boolean;
  is_accessible: boolean;
  admin_emails: string[];
  is_root: boolean;
  company_profile: Record<string, string> | null;
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
  user_id: string;
  is_super_admin: boolean;
  workspace_mode: string;
  assignments: {
    org_unit_id: string;
    org_unit_name: string;
    role_name: string;
    permissions: string[];
  }[];
}

/* ─── Constants ─── */

const UNIT_TYPES = [
  { value: "company", label: "Company" },
  { value: "division", label: "Division" },
  { value: "client_account", label: "Client Account" },
  { value: "region", label: "Region" },
  { value: "team", label: "Team" },
] as const;

const TYPE_LABELS: Record<string, string> = {
  company: "Company",
  division: "Division",
  client_account: "Client Account",
  region: "Region",
  team: "Team",
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

  // Company profile edit
  const [editingProfile, setEditingProfile] = useState(false);
  const [profileForm, setProfileForm] = useState<Record<string, string>>({});
  const [savingProfile, setSavingProfile] = useState(false);

  // Add sub-unit
  const [showAddSubUnit, setShowAddSubUnit] = useState(false);
  const [subUnitName, setSubUnitName] = useState("");
  const [subUnitType, setSubUnitType] = useState("division");
  const [creatingSubUnit, setCreatingSubUnit] = useState(false);

  // Delete unit
  const [deleting, setDeleting] = useState(false);

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

  const canDelete = useMemo(() => {
    if (!me || !unit) return false;
    if (unit.is_root) return false;
    if (me.is_super_admin) return true;
    if (unit.admin_delete_disabled) return false;
    if (!unit.deletable_by) return false;
    const isAdmin = me.assignments.some((a) => a.org_unit_id === unitId && a.role_name === "Admin");
    return isAdmin && me.user_id === unit.deletable_by;
  }, [me, unit, unitId]);

  const createableTypes = UNIT_TYPES.filter((t) => {
    if (t.value === "company") return false;
    if (t.value === "client_account" && me?.workspace_mode !== "agency") return false;
    return true;
  });

  // Don't exclude existing members — same user can have multiple roles
  const filteredUsers = useMemo(() => {
    if (!userSearch.trim()) return [];  // Only show dropdown when user is typing
    const q = userSearch.toLowerCase();
    return tenantUsers.filter(
      (u) => u.email.toLowerCase().includes(q) || (u.full_name || "").toLowerCase().includes(q),
    );
  }, [tenantUsers, userSearch]);

  // For a selected user, filter out roles they already have in this unit
  const availableRolesForUser = useMemo(() => {
    if (!addUserId) return availableRoles;
    const existingMember = members.find((m) => m.user_id === addUserId);
    if (!existingMember) return availableRoles;
    const existingRoleIds = new Set(existingMember.roles.map((r) => r.role_id));
    return availableRoles.filter((r) => !existingRoleIds.has(r.id));
  }, [addUserId, members, availableRoles]);

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

  async function handleSaveProfile() {
    setError("");
    setSavingProfile(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({
          set_company_profile: true,
          company_profile: profileForm,
        }),
      });
      setEditingProfile(false);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleCreateSubUnit(e: React.FormEvent) {
    e.preventDefault();
    if (!subUnitName.trim()) return;
    setError("");
    setCreatingSubUnit(true);
    try {
      const token = await getToken();
      if (!token) return;
      const newUnit = await apiFetch<OrgUnit>("/api/org-units", {
        method: "POST",
        token,
        body: JSON.stringify({
          name: subUnitName.trim(),
          unit_type: subUnitType,
          parent_unit_id: unitId,
        }),
      });
      setSubUnitName("");
      setSubUnitType("division");
      setShowAddSubUnit(false);
      router.push(`/settings/org-units/${newUnit.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create sub-unit");
    } finally {
      setCreatingSubUnit(false);
    }
  }

  async function handleDeleteUnit() {
    setError("");
    setDeleting(true);
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}`, {
        method: "DELETE",
        token,
      });
      // Navigate to parent unit if nested, or back to list
      const parentId = unit?.parent_unit_id;
      if (parentId) {
        router.push(`/settings/org-units/${parentId}`);
      } else {
        router.push("/settings/org-units");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete unit");
      setDeleting(false);
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

  if (!unit.is_accessible) {
    return (
      <div className="max-w-4xl">
        <button
          onClick={() => router.push("/settings/org-units")}
          className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-700 mb-6 cursor-pointer transition-colors duration-100"
        >
          <IconArrowLeft className="w-3.5 h-3.5" />
          All Units
        </button>

        <div className="bg-white border border-zinc-200 rounded-xl p-8 text-center">
          <div className="w-12 h-12 rounded-full bg-zinc-100 flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-zinc-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-zinc-900 mb-2">
            You don&apos;t have access to {unit.name}
          </h2>
          <p className="text-sm text-zinc-500 max-w-md mx-auto leading-relaxed">
            You are not assigned any role in this unit.
            {unit.admin_emails.length > 0 ? (
              <>
                {" "}Ask{" "}
                <span className="font-medium text-zinc-700">
                  {unit.admin_emails.join(" or ")}
                </span>{" "}
                to grant you access.
              </>
            ) : (
              " Contact your super admin to get access."
            )}
          </p>
        </div>
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
                  const isClickable = !isLast && ancestor.is_accessible;
                  return (
                    <span key={ancestor.id} className="flex items-center gap-1">
                      {isLast ? (
                        <span className="text-sm font-semibold text-zinc-900">
                          {ancestor.name}
                        </span>
                      ) : isClickable ? (
                        <button
                          onClick={() => router.push(`/settings/org-units/${ancestor.id}`)}
                          className="text-sm text-green-600 hover:text-green-700 hover:underline cursor-pointer"
                        >
                          {ancestor.name}
                        </button>
                      ) : (
                        <span className="text-sm text-zinc-400">
                          {ancestor.name}
                        </span>
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
                {unit.unit_type !== "company" && (
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
                )}
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
                  {unit.created_by_email && (
                    <span className="text-xs text-zinc-400">
                      by {unit.created_by_email}
                    </span>
                  )}
                </div>
              </div>
              {canManage && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => { setEditName(unit.name); setEditType(unit.unit_type); setEditing(true); }}
                    className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-700 border border-zinc-200 rounded-lg px-3 py-1.5 cursor-pointer transition-colors duration-150"
                  >
                    <IconPencil />
                    Edit
                  </button>
                  {canManage && (
                    <button
                      onClick={() => setShowAddSubUnit(!showAddSubUnit)}
                      className="inline-flex items-center gap-1.5 text-sm text-green-600 hover:text-green-700 border border-green-200 rounded-lg px-3 py-1.5 cursor-pointer transition-colors duration-150"
                    >
                      <IconPlus className="w-3.5 h-3.5" />
                      Sub-unit
                    </button>
                  )}
                  {canDelete && (
                    <button
                      onClick={() =>
                        setConfirmAction({
                          message: `Permanently delete "${unit.name}"? This cannot be undone. The unit must have no sub-units or members.`,
                          onConfirm: handleDeleteUnit,
                        })
                      }
                      disabled={deleting}
                      className="inline-flex items-center gap-1.5 text-sm text-red-500 hover:text-red-600 border border-red-200 rounded-lg px-3 py-1.5 cursor-pointer transition-colors duration-150 disabled:opacity-50"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                      </svg>
                      {deleting ? "Deleting..." : "Delete"}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ─── Company Profile ─── */}
        {(unit.unit_type === "company" || unit.unit_type === "client_account") && (
          <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-zinc-900">Company Profile</h2>
              {canManage && !editingProfile && (
                <button
                  onClick={() => {
                    setProfileForm({
                      display_name: unit.company_profile?.display_name || "",
                      industry: unit.company_profile?.industry || "",
                      company_size: unit.company_profile?.company_size || "",
                      culture_summary: unit.company_profile?.culture_summary || "",
                      strong_hire: unit.company_profile?.strong_hire || "",
                      brand_voice: unit.company_profile?.brand_voice || "",
                    });
                    setEditingProfile(true);
                  }}
                  className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-700 border border-zinc-200 rounded-lg px-3 py-1.5 cursor-pointer transition-colors duration-150"
                >
                  <IconPencil />
                  Edit Profile
                </button>
              )}
            </div>

            {editingProfile ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="profile-display-name" className="block text-xs font-medium text-zinc-600 mb-1">Display Name</label>
                    <input
                      id="profile-display-name"
                      type="text"
                      value={profileForm.display_name || ""}
                      onChange={(e) => setProfileForm({ ...profileForm, display_name: e.target.value })}
                      className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label htmlFor="profile-industry" className="block text-xs font-medium text-zinc-600 mb-1">Industry</label>
                    <input
                      id="profile-industry"
                      type="text"
                      value={profileForm.industry || ""}
                      onChange={(e) => setProfileForm({ ...profileForm, industry: e.target.value })}
                      className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="profile-company-size" className="block text-xs font-medium text-zinc-600 mb-1">Company Size</label>
                    <select
                      id="profile-company-size"
                      value={profileForm.company_size || ""}
                      onChange={(e) => setProfileForm({ ...profileForm, company_size: e.target.value })}
                      className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                    >
                      <option value="">Select...</option>
                      <option value="Startup">Startup</option>
                      <option value="SMB">SMB</option>
                      <option value="Enterprise">Enterprise</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="profile-brand-voice" className="block text-xs font-medium text-zinc-600 mb-1">Brand Voice</label>
                    <select
                      id="profile-brand-voice"
                      value={profileForm.brand_voice || ""}
                      onChange={(e) => setProfileForm({ ...profileForm, brand_voice: e.target.value })}
                      className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                    >
                      <option value="">Select...</option>
                      <option value="Professional">Professional</option>
                      <option value="Conversational">Conversational</option>
                      <option value="Technical">Technical</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label htmlFor="profile-culture" className="block text-xs font-medium text-zinc-600 mb-1">Culture Summary</label>
                  <textarea
                    id="profile-culture"
                    rows={3}
                    value={profileForm.culture_summary || ""}
                    onChange={(e) => setProfileForm({ ...profileForm, culture_summary: e.target.value })}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent resize-none"
                  />
                </div>
                <div>
                  <label htmlFor="profile-strong-hire" className="block text-xs font-medium text-zinc-600 mb-1">What a Strong Hire Looks Like</label>
                  <textarea
                    id="profile-strong-hire"
                    rows={3}
                    value={profileForm.strong_hire || ""}
                    onChange={(e) => setProfileForm({ ...profileForm, strong_hire: e.target.value })}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent resize-none"
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={handleSaveProfile}
                    disabled={savingProfile}
                    className="bg-green-600 text-white px-3.5 py-1.5 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
                  >
                    {savingProfile ? "Saving..." : "Save Profile"}
                  </button>
                  <button
                    onClick={() => setEditingProfile(false)}
                    className="text-sm text-zinc-500 hover:text-zinc-700 px-3 py-1.5 cursor-pointer"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
                <div>
                  <dt className="text-xs font-medium text-zinc-400">Display Name</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5">{unit.company_profile?.display_name || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-zinc-400">Industry</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5">{unit.company_profile?.industry || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-zinc-400">Company Size</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5">{unit.company_profile?.company_size || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-zinc-400">Brand Voice</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5">{unit.company_profile?.brand_voice || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs font-medium text-zinc-400">Culture Summary</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5 whitespace-pre-wrap">{unit.company_profile?.culture_summary || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs font-medium text-zinc-400">What a Strong Hire Looks Like</dt>
                  <dd className="text-sm text-zinc-900 mt-0.5 whitespace-pre-wrap">{unit.company_profile?.strong_hire || <span className="text-zinc-300">Not set</span>}</dd>
                </div>
              </dl>
            )}
          </div>
        )}

        {/* ─── Deletion Settings (super admin only) ─── */}
        {me?.is_super_admin && unit && (
          <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
            <h2 className="text-sm font-semibold text-zinc-900 mb-4">Deletion Settings</h2>
            <div className="space-y-4">
              {/* Lock toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-zinc-700">Lock deletion</p>
                  <p className="text-xs text-zinc-400">When enabled, only a super admin can delete this unit</p>
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    const token = await getToken();
                    if (!token) return;
                    try {
                      await apiFetch(`/api/org-units/${unitId}`, {
                        method: "PUT",
                        token,
                        body: JSON.stringify({ admin_delete_disabled: !unit.admin_delete_disabled }),
                      });
                      await loadAll();
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Failed to update");
                    }
                  }}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200 cursor-pointer ${
                    unit.admin_delete_disabled ? "bg-green-600" : "bg-zinc-200"
                  }`}
                  role="switch"
                  aria-checked={unit.admin_delete_disabled}
                  aria-label="Lock deletion"
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white transition-transform duration-200 ${
                      unit.admin_delete_disabled ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>

              {/* Reassign deletable_by */}
              {!unit.admin_delete_disabled && (
                <div>
                  <label htmlFor="deletable-by" className="block text-sm text-zinc-700 mb-1">
                    Admin authorized to delete
                  </label>
                  <p className="text-xs text-zinc-400 mb-2">
                    Only this admin (and super admins) can delete this unit
                  </p>
                  <select
                    id="deletable-by"
                    value={unit.deletable_by || ""}
                    onChange={async (e) => {
                      const token = await getToken();
                      if (!token) return;
                      try {
                        await apiFetch(`/api/org-units/${unitId}`, {
                          method: "PUT",
                          token,
                          body: JSON.stringify({ deletable_by: e.target.value || null }),
                        });
                        await loadAll();
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Failed to update");
                      }
                    }}
                    className="w-full max-w-xs border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                  >
                    <option value="">None (only super admin)</option>
                    {members
                      .filter((m) => m.roles.some((r) => r.role_name === "Admin"))
                      .map((m) => (
                        <option key={m.user_id} value={m.user_id}>
                          {m.full_name || m.email}
                        </option>
                      ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ─── Add Sub-unit form ─── */}
        {showAddSubUnit && canManage && (
          <form onSubmit={handleCreateSubUnit} className="bg-white border border-green-200 rounded-xl p-5 mb-6 space-y-4">
            <h2 className="text-sm font-semibold text-zinc-900">
              Create Sub-unit under {unit.name}
            </h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="subunit-name" className="block text-xs font-medium text-zinc-600 mb-1">Name</label>
                <input
                  id="subunit-name"
                  type="text"
                  required
                  value={subUnitName}
                  onChange={(e) => setSubUnitName(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
                  placeholder="e.g., Frontend Team"
                  autoFocus
                />
              </div>
              <div>
                <label htmlFor="subunit-type" className="block text-xs font-medium text-zinc-600 mb-1">Type</label>
                <select
                  id="subunit-type"
                  value={subUnitType}
                  onChange={(e) => setSubUnitType(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                >
                  {createableTypes.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => { setShowAddSubUnit(false); setSubUnitName(""); }}
                className="text-sm text-zinc-500 hover:text-zinc-700 px-3 py-1.5 cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creatingSubUnit || !subUnitName.trim()}
                className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
              >
                {creatingSubUnit ? "Creating..." : "Create Sub-unit"}
              </button>
            </div>
          </form>
        )}

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
                        onClick={() => { setAddUserId(""); setAddUserEmail(""); setUserSearch(""); setAddRoleId(""); }}
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
                              onClick={() => { setAddUserId(u.id); setAddUserEmail(u.email); setUserSearch(""); setAddRoleId(""); }}
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

                {/* Role picker — filters out roles the selected user already has */}
                <div>
                  <label htmlFor="add-role" className="block text-xs font-medium text-zinc-600 mb-1">Role</label>
                  {addUserId && availableRolesForUser.length === 0 ? (
                    <p className="text-xs text-zinc-400 border border-zinc-200 rounded-lg px-3 py-2.5">
                      This user already has all available roles in this unit.
                    </p>
                  ) : (
                    <select
                      id="add-role"
                      value={addRoleId}
                      onChange={(e) => setAddRoleId(e.target.value)}
                      className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                    >
                      <option value="">Select a role...</option>
                      {availableRolesForUser.map((r) => (
                        <option key={r.id} value={r.id}>{r.name}</option>
                      ))}
                    </select>
                  )}
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
