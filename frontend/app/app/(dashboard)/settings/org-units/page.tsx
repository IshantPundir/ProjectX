"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

/* ─── Types ─── */

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

function IconPlus({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
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

function IconUsers({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
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

/* ─── Page ─── */

export default function OrgUnitsPage() {
  const router = useRouter();
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

  const getToken = useCallback(async () => {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      window.location.href = "/login";
      return null;
    }
    return session.access_token;
  }, []);

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

  useEffect(() => { loadUnits(); }, [loadUnits]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
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
      router.push(`/settings/org-units/${newUnit.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create unit");
      setCreating(false);
    }
  }

  const tree = useMemo(() => buildTree(units), [units]);

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">Organizational Units</h1>
          <p className="text-xs text-zinc-400 mt-0.5">
            {units.length} unit{units.length !== 1 ? "s" : ""} in your organization
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

      {error && (
        <div
          role="alert"
          className="flex items-start justify-between text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4"
        >
          <span>{error}</span>
          <button onClick={() => setError("")} className="ml-2 shrink-0 cursor-pointer" aria-label="Dismiss error">
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
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
          </div>
          {units.length > 0 && (
            <div>
              <label htmlFor="create-parent" className="block text-xs font-medium text-zinc-600 mb-1">
                Parent Unit <span className="font-normal text-zinc-400">(optional)</span>
              </label>
              <select
                id="create-parent"
                value={createParent}
                onChange={(e) => setCreateParent(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
              >
                <option value="">None (top-level)</option>
                {tree.map(({ unit: u, depth }) => (
                  <option key={u.id} value={u.id}>{"\u00A0\u00A0".repeat(depth)}{u.name}</option>
                ))}
              </select>
            </div>
          )}
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={creating || !createName.trim()}
              className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
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
            <div key={i} className="h-14 bg-zinc-100 rounded-lg animate-pulse" />
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
        <div className="bg-white border border-zinc-200 rounded-xl divide-y divide-zinc-100">
          {tree.map(({ unit: u, depth }) => (
            <button
              key={u.id}
              type="button"
              onClick={() => router.push(`/settings/org-units/${u.id}`)}
              className="w-full flex items-center justify-between py-3.5 pr-4 hover:bg-zinc-50 cursor-pointer transition-colors duration-100 text-left group first:rounded-t-xl last:rounded-b-xl"
              style={{ paddingLeft: `${depth * 24 + 16}px` }}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                {depth > 0 && (
                  <IconChevron className="w-2.5 h-2.5 text-zinc-300 shrink-0" />
                )}
                <span className="text-sm font-medium text-zinc-900 truncate">{u.name}</span>
                <span className="bg-zinc-100 text-zinc-500 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0">
                  {TYPE_LABELS[u.unit_type] || u.unit_type}
                </span>
              </div>
              <div className="flex items-center gap-4 shrink-0">
                <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                  <IconUsers className="w-3 h-3" />
                  {u.member_count}
                </span>
                <svg
                  className="w-4 h-4 text-zinc-300 group-hover:text-zinc-500 transition-colors duration-100"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
