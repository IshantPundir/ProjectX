"use client";

import { useEffect, useState, useMemo } from "react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface OrgUnit {
  id: string;
  client_id: string;
  parent_unit_id: string | null;
  name: string;
  unit_type: string;
  member_count: number;
  created_at: string;
}

interface OrgUnitMember {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string | null;        // role within THIS unit
  is_admin: boolean;          // admin of THIS unit
  permissions: string[];
  assignment_type: string;
  assigned_at: string;
}

interface TenantUser {
  id: string;
  email: string;
  full_name: string | null;
  role: string | null;
  is_admin: boolean;
}

const UNIT_TYPES = [
  { value: "client_account", label: "Client Account" },
  { value: "department", label: "Department" },
  { value: "team", label: "Team" },
  { value: "branch", label: "Branch" },
  { value: "region", label: "Region" },
];

const typeLabel: Record<string, string> = {
  client_account: "Client Account",
  department: "Department",
  team: "Team",
  branch: "Branch",
  region: "Region",
};

function buildTree(units: OrgUnit[]): { unit: OrgUnit; depth: number }[] {
  const childrenMap = new Map<string | null, OrgUnit[]>();
  for (const u of units) {
    childrenMap.set(u.parent_unit_id, [...(childrenMap.get(u.parent_unit_id) || []), u]);
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

export default function OrgUnitsPage() {
  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createType, setCreateType] = useState("department");
  const [createParent, setCreateParent] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("");

  // Selected unit panel
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [members, setMembers] = useState<OrgUnitMember[]>([]);
  const [allUsers, setAllUsers] = useState<TenantUser[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [userSearch, setUserSearch] = useState("");

  async function getToken() {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      window.location.href = "/login";
      return null;
    }
    return session.access_token;
  }

  async function loadUnits() {
    try {
      const token = await getToken();
      if (!token) return;
      setUnits(await apiFetch<OrgUnit[]>("/api/org-units", { token }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  async function loadMembers(unitId: string) {
    setMembersLoading(true);
    try {
      const token = await getToken();
      if (!token) return;
      const [m, u] = await Promise.all([
        apiFetch<OrgUnitMember[]>(`/api/org-units/${unitId}/members`, { token }),
        apiFetch<TenantUser[]>("/api/settings/team/members", { token }),
      ]);
      setMembers(m);
      // Filter to users only (not invites)
      setAllUsers((u as (TenantUser & { source?: string })[]).filter((x) => (x as { source?: string }).source === "user"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    } finally {
      setMembersLoading(false);
    }
  }

  useEffect(() => { loadUnits(); }, []);
  useEffect(() => { if (selectedId) loadMembers(selectedId); }, [selectedId]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      const newUnit = await apiFetch<OrgUnit>("/api/org-units", {
        method: "POST", token,
        body: JSON.stringify({ name: createName, unit_type: createType, parent_unit_id: createParent || null }),
      });
      setCreateName("");
      setCreateType("department");
      setCreateParent("");
      setShowCreate(false);
      await loadUnits();
      setSelectedId(newUnit.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
    } finally {
      setCreating(false);
    }
  }

  async function handleEdit(unitId: string) {
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${unitId}`, {
        method: "PUT", token,
        body: JSON.stringify({ name: editName, unit_type: editType }),
      });
      setEditId(null);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    }
  }

  async function handleAssign(userId: string) {
    if (!selectedId) return;
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedId}/members`, {
        method: "POST", token,
        body: JSON.stringify({ user_id: userId }),
      });
      setUserSearch("");
      await loadMembers(selectedId);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign");
    }
  }

  async function handleUnassign(userId: string) {
    if (!selectedId) return;
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedId}/members/${userId}`, {
        method: "DELETE", token,
      });
      await loadMembers(selectedId);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove");
    }
  }

  const tree = buildTree(units);
  const selectedUnit = units.find((u) => u.id === selectedId);

  // Searchable user list — exclude users already in this unit
  const memberIds = new Set(members.map((m) => m.user_id));
  const filteredUsers = useMemo(() => {
    const available = allUsers.filter((u) => !memberIds.has(u.id));
    if (!userSearch.trim()) return available.slice(0, 8);
    const q = userSearch.toLowerCase();
    return available.filter(
      (u) => u.email.toLowerCase().includes(q) || (u.full_name || "").toLowerCase().includes(q),
    );
  }, [allUsers, memberIds, userSearch]);

  return (
    <div className="flex gap-6 min-h-[600px]">
      {/* Left panel: unit tree */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-lg font-semibold text-zinc-900">Org Units</h1>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="bg-green-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-green-700"
          >
            {showCreate ? "Cancel" : "+ New Unit"}
          </button>
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
        )}

        {showCreate && (
          <form onSubmit={handleCreate} className="bg-white border border-zinc-200 rounded-lg p-4 mb-4 space-y-3">
            <h2 className="text-sm font-medium text-zinc-900">Create Organizational Unit</h2>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-zinc-600 mb-1">Name</label>
                <input type="text" required value={createName} onChange={(e) => setCreateName(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
                  placeholder="e.g., Engineering" />
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-600 mb-1">Type</label>
                <select value={createType} onChange={(e) => setCreateType(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-green-600">
                  {UNIT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
            </div>
            {units.length > 0 && (
              <div>
                <label className="block text-xs font-medium text-zinc-600 mb-1">Parent Unit (optional — makes this a sub-unit)</label>
                <select value={createParent} onChange={(e) => setCreateParent(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-green-600">
                  <option value="">None (top-level)</option>
                  {tree.map(({ unit: u, depth }) => (
                    <option key={u.id} value={u.id}>{"  ".repeat(depth)}{u.name}</option>
                  ))}
                </select>
              </div>
            )}
            <p className="text-xs text-zinc-400">You will be auto-assigned as an admin. For sub-units, parent admins are inherited.</p>
            <button type="submit" disabled={creating}
              className="w-full bg-green-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-green-700 disabled:opacity-50">
              {creating ? "Creating..." : "Create Unit"}
            </button>
          </form>
        )}

        {loading ? (
          <p className="text-sm text-zinc-500">Loading...</p>
        ) : units.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-zinc-400 text-sm mb-2">No organizational units yet</p>
            <button onClick={() => setShowCreate(true)}
              className="text-sm text-green-600 hover:underline">Create your first unit</button>
          </div>
        ) : (
          <div className="space-y-1">
            {tree.map(({ unit: u, depth }) => {
              const isSelected = selectedId === u.id;
              const isEditing = editId === u.id;

              return (
                <div
                  key={u.id}
                  style={{ paddingLeft: `${depth * 20 + 12}px` }}
                  className={`flex items-center justify-between py-2.5 pr-3 rounded-lg cursor-pointer transition-colors ${
                    isSelected ? "bg-green-50 border border-green-200" : "hover:bg-zinc-50 border border-transparent"
                  }`}
                  onClick={() => !isEditing && setSelectedId(isSelected ? null : u.id)}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {depth > 0 && <span className="text-zinc-300 text-xs">└</span>}
                    {isEditing ? (
                      <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                        <input value={editName} onChange={(e) => setEditName(e.target.value)}
                          className="border border-zinc-300 rounded px-2 py-1 text-sm w-32" />
                        <select value={editType} onChange={(e) => setEditType(e.target.value)}
                          className="border border-zinc-300 rounded px-2 py-1 text-xs bg-white">
                          {UNIT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                        <button onClick={() => handleEdit(u.id)} className="text-xs text-green-600 hover:underline">Save</button>
                        <button onClick={() => setEditId(null)} className="text-xs text-zinc-400 hover:underline">Cancel</button>
                      </div>
                    ) : (
                      <>
                        <span className="text-sm font-medium text-zinc-900 truncate">{u.name}</span>
                        <span className="bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0">
                          {typeLabel[u.unit_type] || u.unit_type}
                        </span>
                      </>
                    )}
                  </div>
                  {!isEditing && (
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs text-zinc-400">{u.member_count} members</span>
                      <button onClick={(e) => { e.stopPropagation(); setEditId(u.id); setEditName(u.name); setEditType(u.unit_type); }}
                        className="text-xs text-zinc-400 hover:text-zinc-600">Edit</button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Right panel: unit members */}
      {selectedUnit && (
        <div className="w-96 shrink-0 bg-white border border-zinc-200 rounded-lg p-5 self-start">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-semibold text-zinc-900">{selectedUnit.name}</h2>
              <p className="text-xs text-zinc-400 mt-0.5">
                {typeLabel[selectedUnit.unit_type]} · {members.length} members
              </p>
            </div>
            <button onClick={() => setSelectedId(null)} className="text-xs text-zinc-400 hover:text-zinc-600">Close</button>
          </div>

          {/* Search + assign users */}
          <div className="mb-4">
            <label className="block text-xs font-medium text-zinc-600 mb-1">Add member</label>
            <input
              type="text"
              value={userSearch}
              onChange={(e) => setUserSearch(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
              placeholder="Search by email or name..."
            />
            {(userSearch || filteredUsers.length > 0) && filteredUsers.length > 0 && (
              <div className="mt-1 border border-zinc-200 rounded-lg bg-white max-h-40 overflow-y-auto shadow-sm">
                {filteredUsers.map((u) => (
                  <button
                    key={u.id}
                    onClick={() => handleAssign(u.id)}
                    className="w-full text-left px-3 py-2 hover:bg-zinc-50 border-b border-zinc-100 last:border-0"
                  >
                    <p className="text-sm text-zinc-900">{u.email}</p>
                    {u.full_name && <p className="text-xs text-zinc-500">{u.full_name}</p>}
                  </button>
                ))}
              </div>
            )}
            {userSearch && filteredUsers.length === 0 && (
              <p className="text-xs text-zinc-400 mt-1 px-1">No matching users available</p>
            )}
          </div>

          {/* Member list */}
          {membersLoading ? (
            <p className="text-xs text-zinc-400">Loading...</p>
          ) : members.length === 0 ? (
            <p className="text-xs text-zinc-400">No members yet</p>
          ) : (
            <div className="space-y-2">
              {members.map((m) => (
                <div key={m.user_id} className="border border-zinc-100 rounded-lg p-3 hover:bg-zinc-50">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm text-zinc-900 truncate">{m.email}</p>
                    <button onClick={() => handleUnassign(m.user_id)}
                      className="text-xs text-red-500 hover:underline shrink-0">Remove</button>
                  </div>
                  <div className="flex items-center gap-2">
                    <select
                      value={m.role || ""}
                      onChange={async (e) => {
                        const token = await getToken();
                        if (!token) return;
                        await apiFetch(`/api/org-units/${selectedId}/members/${m.user_id}`, {
                          method: "PUT", token,
                          body: JSON.stringify({ role: e.target.value || null }),
                        });
                        await loadMembers(selectedId!);
                      }}
                      className="border border-zinc-200 rounded px-2 py-1 text-xs bg-white"
                    >
                      <option value="">No role</option>
                      <option value="Admin">Admin</option>
                      <option value="Recruiter">Recruiter</option>
                      <option value="Hiring Manager">Hiring Manager</option>
                      <option value="Interviewer">Interviewer</option>
                      <option value="Observer">Observer</option>
                    </select>
                    {m.is_admin && (
                      <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded font-medium">Unit Admin</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
