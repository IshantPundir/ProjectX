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
    const children = childrenMap.get(parentId) || [];
    for (const child of children) {
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

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [unitType, setUnitType] = useState("department");
  const [parentId, setParentId] = useState("");
  const [creating, setCreating] = useState(false);

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

  useEffect(() => { loadUnits(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");

    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/org-units", {
        method: "POST",
        token,
        body: JSON.stringify({
          name,
          unit_type: unitType,
          parent_unit_id: parentId || null,
        }),
      });
      setName("");
      setUnitType("department");
      setParentId("");
      setShowForm(false);
      await loadUnits();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create org unit");
    } finally {
      setCreating(false);
    }
  }

  const tree = buildTree(units);

  return (
    <>
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
          <h2 className="text-sm font-medium text-zinc-900 mb-3">New Organizational Unit</h2>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-xs font-medium text-zinc-600 mb-1">Name</label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
                placeholder="e.g., NYC Office"
              />
            </div>
            <div className="w-40">
              <label className="block text-xs font-medium text-zinc-600 mb-1">Type</label>
              <select
                value={unitType}
                onChange={(e) => setUnitType(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
              >
                {UNIT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            {units.length > 0 && (
              <div className="w-48">
                <label className="block text-xs font-medium text-zinc-600 mb-1">Parent (optional)</label>
                <select
                  value={parentId}
                  onChange={(e) => setParentId(e.target.value)}
                  className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
                >
                  <option value="">None (top-level)</option>
                  {units.map((u) => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
                </select>
              </div>
            )}
            <button
              type="submit"
              disabled={creating}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create"}
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
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Created</th>
              </tr>
            </thead>
            <tbody>
              {tree.map(({ unit: u, depth }) => (
                <tr key={u.id} className="border-b border-zinc-100 last:border-0">
                  <td className="px-4 py-2.5 font-medium text-zinc-900">
                    <span style={{ paddingLeft: `${depth * 24}px` }} className="flex items-center gap-1.5">
                      {depth > 0 && (
                        <span className="text-zinc-300">└</span>
                      )}
                      {u.name}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-xs">
                      {typeLabel[u.unit_type] || u.unit_type}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-zinc-400">{new Date(u.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
