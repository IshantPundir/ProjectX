"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { createClient } from "@/lib/supabase/client";
import { orgUnitsApi, type MeData, type OrgUnit } from "@/lib/api/org-units";
import {
  CompanyProfileForm,
  type CompanyProfile,
} from "@/components/dashboard/company-profile-form";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { OrgUnitCanvas } from "@/components/dashboard/org-units/OrgUnitCanvas";
import { OrgUnitDetailPanel } from "@/components/dashboard/org-units/OrgUnitDetailPanel";
import { OrgUnitTreeListFallback } from "@/components/dashboard/org-units/OrgUnitTreeListFallback";

/* ─── Constants ─── */

const UNIT_TYPES = [
  { value: "company", label: "Company" },
  { value: "division", label: "Division" },
  { value: "client_account", label: "Client Account" },
  { value: "region", label: "Region" },
  { value: "team", label: "Team" },
] as const;

/* ─── Icons ─── */

function IconPlus({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
    </svg>
  );
}

function IconX({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function IconBuilding({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21"
      />
    </svg>
  );
}

/* ─── Helpers ─── */

// Flatten the units tree for the parent-unit <select> — preserves the
// indented visual that the old list view used, without pulling in the full
// fallback component.
function flattenForSelect(
  units: OrgUnit[],
): { unit: OrgUnit; depth: number }[] {
  const childrenMap = new Map<string | null, OrgUnit[]>();
  for (const u of units) {
    childrenMap.set(u.parent_unit_id, [
      ...(childrenMap.get(u.parent_unit_id) || []),
      u,
    ]);
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

  // Canvas selection (side panel driven by this)
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createType, setCreateType] = useState("division");
  const [createParent, setCreateParent] = useState("");
  const [creating, setCreating] = useState(false);

  // Client account profile dialog — opened when the user submits the create
  // form with `createType === "client_account"`. The dialog contains the
  // shared CompanyProfileForm (Phase 2A 4-field shape) and drives the actual
  // POST /api/org-units call via handleClientAccountProfileSubmit.
  const [showProfileDialog, setShowProfileDialog] = useState(false);

  const getToken = useCallback(async () => {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
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
        orgUnitsApi.list(token),
        orgUnitsApi.me(token),
      ]);
      setUnits(unitsData);
      setMe(meData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    loadUnits();
  }, [loadUnits]);

  // Close the side panel on Escape.
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape" && selectedUnitId) {
        setSelectedUnitId(null);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [selectedUnitId]);

  async function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (createType === "client_account") {
      // Open the CompanyProfile dialog — it drives the actual create.
      setError("");
      setShowProfileDialog(true);
      return;
    }
    try {
      await doCreate(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create unit");
    }
  }

  async function handleClientAccountProfileSubmit(profile: CompanyProfile) {
    // Called by CompanyProfileForm after client-side Zod validation passes.
    // Re-throw on failure so RHF resets `isSubmitting` and the user can
    // correct + retry without closing the dialog.
    try {
      await doCreate(profile);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create unit");
      throw err;
    }
  }

  // Creates the org unit and (on success) navigates to the new unit's detail
  // page. Throws on failure so callers can reset submission state / display
  // the error. Does NOT setError() itself — that's the caller's job, so the
  // CompanyProfileForm's isSubmitting state can propagate cleanly.
  async function doCreate(companyProfile: CompanyProfile | null) {
    setCreating(true);
    try {
      const token = await getToken();
      if (!token) return;
      const newUnit = await orgUnitsApi.create(token, {
        name: createName.trim(),
        unit_type: createType,
        parent_unit_id: createParent || null,
        company_profile: companyProfile,
      });
      setCreateName("");
      setCreateType("division");
      setCreateParent("");
      setShowCreate(false);
      setShowProfileDialog(false);
      router.push(`/settings/org-units/${newUnit.id}`);
    } finally {
      setCreating(false);
    }
  }

  // Called from the side panel's "Add child unit" action — presets the parent
  // in the create form and opens it.
  function handleAddChild(parentId: string) {
    setCreateParent(parentId);
    setShowCreate(true);
    setSelectedUnitId(null);
  }

  const selectOptions = useMemo(() => flattenForSelect(units), [units]);
  const selectedUnit = useMemo(
    () => units.find((u) => u.id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const createableTypes = UNIT_TYPES.filter((t) => {
    if (t.value === "company") return false;
    if (t.value === "client_account" && me?.workspace_mode !== "agency")
      return false;
    return true;
  });

  return (
    <div className="max-w-[1600px] mx-auto">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">
            Organizational Units
          </h1>
          <p className="text-xs text-zinc-400 mt-0.5">
            {units.length} unit{units.length !== 1 ? "s" : ""} in your
            organization
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
          <button
            onClick={() => setError("")}
            className="ml-2 shrink-0 cursor-pointer"
            aria-label="Dismiss error"
          >
            <IconX className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Create form */}
      {showCreate && me?.is_super_admin && (
        <form
          onSubmit={handleCreateSubmit}
          className="bg-white border border-zinc-200 rounded-xl p-5 mb-5 space-y-4"
        >
          <h2 className="text-sm font-semibold text-zinc-900">
            Create Organizational Unit
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label
                htmlFor="create-name"
                className="block text-xs font-medium text-zinc-600 mb-1"
              >
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
              <label
                htmlFor="create-type"
                className="block text-xs font-medium text-zinc-600 mb-1"
              >
                Type
              </label>
              <select
                id="create-type"
                value={createType}
                onChange={(e) => setCreateType(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
              >
                {createableTypes.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {units.length > 0 && (
            <div>
              <label
                htmlFor="create-parent"
                className="block text-xs font-medium text-zinc-600 mb-1"
              >
                Parent Unit{" "}
                <span className="font-normal text-zinc-400">(optional)</span>
              </label>
              <select
                id="create-parent"
                value={createParent}
                onChange={(e) => setCreateParent(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
              >
                <option value="">None (top-level)</option>
                {selectOptions.map(({ unit: u, depth }) => (
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
              className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
            >
              {creating ? "Creating..." : "Create Unit"}
            </button>
          </div>
        </form>
      )}

      {/* Content: canvas (desktop) + fallback (mobile) */}
      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-14 bg-zinc-100 rounded-lg animate-pulse"
            />
          ))}
        </div>
      ) : units.length === 0 ? (
        <div className="text-center py-20 border border-dashed border-zinc-200 rounded-xl">
          <IconBuilding className="w-8 h-8 text-zinc-300 mx-auto mb-3" />
          <p className="text-sm text-zinc-500 mb-1">
            No organizational units yet
          </p>
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
        <>
          {/* Desktop canvas */}
          <div className="hidden md:block h-[calc(100vh-18rem)] min-h-[500px]">
            <OrgUnitCanvas
              units={units}
              selectedUnitId={selectedUnitId}
              onNodeClick={setSelectedUnitId}
            />
          </div>
          {/* Mobile fallback list */}
          <div className="md:hidden">
            <OrgUnitTreeListFallback
              units={units}
              onUnitClick={(id) => router.push(`/settings/org-units/${id}`)}
            />
          </div>
        </>
      )}

      {/* Side panel — driven by canvas selection */}
      {selectedUnit && (
        <OrgUnitDetailPanel
          unit={selectedUnit}
          onClose={() => setSelectedUnitId(null)}
          onAddChild={handleAddChild}
          canAddChild={me?.is_super_admin ?? false}
        />
      )}

      {/* Client Account Profile Dialog —
          Opened when the create form is submitted with createType === "client_account".
          Hosts the shared CompanyProfileForm which owns its own validation and
          submission state. On success, handleClientAccountProfileSubmit calls
          doCreate which navigates away. */}
      <Dialog
        open={showProfileDialog}
        onOpenChange={(open) => {
          // Prevent closing while a create is in flight — the button is also
          // disabled via CompanyProfileForm's isSubmitting, but protect against
          // backdrop clicks / Escape too.
          if (creating) return;
          setShowProfileDialog(open);
        }}
      >
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Client Account Profile</DialogTitle>
            <DialogDescription>
              Set up the profile for{" "}
              <span className="font-medium text-zinc-900">
                {createName.trim()}
              </span>
              . This describes the end client — the company your recruiters are
              hiring <em>for</em>. It feeds the AI when generating JD
              enhancements and interview questions for this client&apos;s
              roles.
            </DialogDescription>
          </DialogHeader>
          <CompanyProfileForm
            onSubmit={handleClientAccountProfileSubmit}
            submitLabel="Create Client Account"
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
