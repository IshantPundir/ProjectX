"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface OrgUnit {
  id: string;
  client_id: string;
  parent_unit_id: string | null;
  name: string;
  unit_type: string;
  created_at: string;
}

interface OrgUnitMember {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_admin: boolean;
  assignment_type: "primary" | "assigned";
  assigned_at: string;
}

interface TeamUser {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
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
    const key = u.parent_unit_id;
    if (!childrenMap.has(key)) childrenMap.set(key, []);
    childrenMap.get(key)!.push(u);
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
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [unitType, setUnitType] = useState("department");
  const [parentId, setParentId] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("");

  // Members panel
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [members, setMembers] = useState<OrgUnitMember[]>([]);
  const [allUsers, setAllUsers] = useState<TeamUser[]>([]);
  const [assignUserId, setAssignUserId] = useState("");
  const [membersLoading, setMembersLoading] = useState(false);

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
      const data = await apiFetch<OrgUnit[]>("/api/org-units", { token });
      setUnits(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load org units");
    } finally {
      setLoading(false);
    }
  }

  async function loadMembers(unitId: string) {
    setMembersLoading(true);
    try {
      const token = await getToken();
      if (!token) return;
      const [memberData, userData] = await Promise.all([
        apiFetch<OrgUnitMember[]>(`/api/org-units/${unitId}/members`, { token }),
        apiFetch<TeamUser[]>("/api/settings/team/members", { token }),
      ]);
      setMembers(memberData);
      setAllUsers(userData.filter((u: TeamUser) => u.source === "user") as TeamUser[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    } finally {
      setMembersLoading(false);
    }
  }

  useEffect(() => { loadUnits(); }, []);

  useEffect(() => {
    if (selectedUnitId) loadMembers(selectedUnitId);
  }, [selectedUnitId]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/org-units", {
        method: "POST", token,
        body: JSON.stringify({ name, unit_type: unitType, parent_unit_id: parentId || null }),
      });
      setName(""); setUnitType("department"); setParentId(""); setShowForm(false);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
    } finally { setCreating(false); }
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
      setEditingId(null);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    }
  }

  async function handleAssign() {
    if (!selectedUnitId || !assignUserId) return;
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedUnitId}/members`, {
        method: "POST", token,
        body: JSON.stringify({ user_id: assignUserId }),
      });
      setAssignUserId("");
      await loadMembers(selectedUnitId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign");
    }
  }

  async function handleUnassign(userId: string) {
    if (!selectedUnitId) return;
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/api/org-units/${selectedUnitId}/members/${userId}`, {
        method: "DELETE", token,
      });
      await loadMembers(selectedUnitId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove");
    }
  }

  const tree = buildTree(units);
  const selectedUnit = units.find((u) => u.id === selectedUnitId);

  // Users available to assign (not already in this unit)
  const memberUserIds = new Set(members.map((m) => m.user_id));
  const assignableUsers = allUsers.filter((u) => !memberUserIds.has(u.id));

  return (
    <div className="flex gap-6">
      {/* Left: org units tree */}
      <div className="flex-1">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-lg font-semibold text-zinc-900">Organizational Units</h1>
          <button
            onClick={() => setShowForm(!showForm)}
            className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700"
          >
            {showForm ? "Cancel" : "+ Create Unit"}
          </button>
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
        )}

        {showForm && (
          <form onSubmit={handleCreate} className="bg-white border border-zinc-200 rounded-lg p-5 mb-6">
            <h2 className="text-sm font-medium text-zinc-900 mb-3">New Unit</h2>
            <div className="flex gap-3 items-end flex-wrap">
              <div className="flex-1 min-w-[160px]">
                <label className="block text-xs font-medium text-zinc-600 mb-1">Name</label>
                <input type="text" required value={name} onChange={(e) => setName(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600" />
              </div>
              <div className="w-40">
                <label className="block text-xs font-medium text-zinc-600 mb-1">Type</label>
                <select value={unitType} onChange={(e) => setUnitType(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-green-600">
                  {UNIT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              {units.length > 0 && (
                <div className="w-44">
                  <label className="block text-xs font-medium text-zinc-600 mb-1">Parent</label>
                  <select value={parentId} onChange={(e) => setParentId(e.target.value)}
                    className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-green-600">
                    <option value="">None</option>
                    {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </div>
              )}
              <button type="submit" disabled={creating}
                className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50">
                {creating ? "..." : "Create"}
              </button>
            </div>
          </form>
        )}

        {loading ? (
          <p className="text-sm text-zinc-500">Loading...</p>
        ) : units.length === 0 ? (
          <p className="text-sm text-zinc-500">No organizational units yet.</p>
        ) : (
          <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-zinc-50 border-b border-zinc-200">
                  <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Name</th>
                  <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Type</th>
                  <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                </tr>
              </thead>
              <tbody>
                {tree.map(({ unit: u, depth }) => (
                  <tr key={u.id} className={`border-b border-zinc-100 last:border-0 ${selectedUnitId === u.id ? "bg-green-50" : ""}`}>
                    <td className="px-4 py-2.5 font-medium text-zinc-900">
                      {editingId === u.id ? (
                        <input value={editName} onChange={(e) => setEditName(e.target.value)}
                          className="border border-zinc-300 rounded px-2 py-1 text-sm w-40" />
                      ) : (
                        <span style={{ paddingLeft: `${depth * 24}px` }} className="flex items-center gap-1.5">
                          {depth > 0 && <span className="text-zinc-300">└</span>}
                          {u.name}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {editingId === u.id ? (
                        <select value={editType} onChange={(e) => setEditType(e.target.value)}
                          className="border border-zinc-300 rounded px-2 py-1 text-xs bg-white">
                          {UNIT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                      ) : (
                        <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-xs">
                          {typeLabel[u.unit_type] || u.unit_type}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 space-x-2">
                      {editingId === u.id ? (
                        <>
                          <button onClick={() => handleEdit(u.id)} className="text-xs text-green-600 hover:underline">Save</button>
                          <button onClick={() => setEditingId(null)} className="text-xs text-zinc-500 hover:underline">Cancel</button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => { setEditingId(u.id); setEditName(u.name); setEditType(u.unit_type); }}
                            className="text-xs text-blue-600 hover:underline">Edit</button>
                          <button onClick={() => setSelectedUnitId(selectedUnitId === u.id ? null : u.id)}
                            className="text-xs text-green-600 hover:underline">
                            {selectedUnitId === u.id ? "Hide" : "Members"}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Right: members panel */}
      {selectedUnit && (
        <div className="w-80 shrink-0">
          <h2 className="text-sm font-semibold text-zinc-900 mb-3">
            {selectedUnit.name} — Members
          </h2>

          {membersLoading ? (
            <p className="text-xs text-zinc-500">Loading...</p>
          ) : (
            <>
              {members.length === 0 ? (
                <p className="text-xs text-zinc-500 mb-4">No members yet.</p>
              ) : (
                <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-4">
                  {members.map((m) => (
                    <div key={m.user_id} className="flex items-center justify-between px-3 py-2 border-b border-zinc-100 last:border-0">
                      <div>
                        <p className="text-sm text-zinc-900">{m.email}</p>
                        <p className="text-xs text-zinc-500">
                          {m.role}
                          {m.is_admin && <span className="ml-1 text-blue-600">Admin</span>}
                          {" · "}
                          <span className={m.assignment_type === "primary" ? "text-green-600" : "text-amber-600"}>
                            {m.assignment_type}
                          </span>
                        </p>
                      </div>
                      {m.assignment_type === "assigned" && (
                        <button onClick={() => handleUnassign(m.user_id)}
                          className="text-xs text-red-600 hover:underline">Remove</button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {assignableUsers.length > 0 && (
                <div className="flex gap-2">
                  <select value={assignUserId} onChange={(e) => setAssignUserId(e.target.value)}
                    className="flex-1 border border-zinc-300 rounded-lg px-2 py-1.5 text-xs bg-white">
                    <option value="">Assign user...</option>
                    {assignableUsers.map((u) => (
                      <option key={u.id} value={u.id}>{u.email}</option>
                    ))}
                  </select>
                  <button onClick={handleAssign} disabled={!assignUserId}
                    className="px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs font-medium hover:bg-green-700 disabled:opacity-50">
                    Add
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
