"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";

import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { jobsApi, type JobPostingSummary } from "@/lib/api/jobs";
import { type OrgUnit } from "@/lib/api/org-units";
import { authApi, type MeResponse } from "@/lib/api/auth";
import { useOrgUnits } from "@/lib/hooks/use-org-units";
import { useCreateOrgUnit } from "@/lib/hooks/use-create-org-unit";
import { applyApiErrorToForm } from "@/lib/api/errors";
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
} from "@/components/px";
import {
  OrgGraph,
  OrgLegend,
  type GraphNodeData,
  type Pressure,
} from "@/components/dashboard/org-units/OrgGraph";

import {
  createOrgUnitSchema,
  type CreateOrgUnitFormValues,
} from "./schema";

const UNIT_TYPES = [
  { value: "division", label: "Division" },
  { value: "client_account", label: "Client account" },
  { value: "region", label: "Region" },
  { value: "team", label: "Team" },
] as const;

function IconPlus({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
      aria-hidden="true"
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
      aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function IconSearch({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

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
  const walk = (parentId: string | null, depth: number) => {
    for (const child of childrenMap.get(parentId) || []) {
      result.push({ unit: child, depth });
      walk(child.id, depth + 1);
    }
  };
  walk(null, 0);
  return result;
}

/** Heuristic: map open-role count → hiring pressure. */
function pressureFor(openRoles: number): Pressure {
  if (openRoles >= 3) return "hot";
  if (openRoles > 0) return "steady";
  return "cool";
}

export default function OrgUnitsPage() {
  const router = useRouter();

  const unitsQuery = useOrgUnits();
  const meQuery = useQuery<MeResponse>({
    queryKey: ["me"],
    queryFn: async () => authApi.me(await getFreshSupabaseToken()),
    staleTime: 60_000,
  });
  const jobsQuery = useQuery<JobPostingSummary[]>({
    queryKey: ["jobs-list"],
    queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
    staleTime: 10_000,
  });

  const units = useMemo(() => unitsQuery.data ?? [], [unitsQuery.data]);
  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data]);
  const me = meQuery.data ?? null;
  const loading = unitsQuery.isLoading || meQuery.isLoading;
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [showProfileDialog, setShowProfileDialog] = useState(false);

  const createForm = useForm<CreateOrgUnitFormValues>({
    resolver: zodResolver(createOrgUnitSchema),
    defaultValues: { name: "", unit_type: "division", parent_unit_id: "" },
  });
  const createMutation = useCreateOrgUnit();

  // Compute open-role count per unit (non-draft = active role)
  const openRolesByUnit = useMemo(() => {
    const m: Record<string, number> = {};
    for (const j of jobs) {
      if (j.status === "draft") continue;
      m[j.org_unit_id] = (m[j.org_unit_id] ?? 0) + 1;
    }
    return m;
  }, [jobs]);

  // Roll openRoles up the tree — a company's total includes all descendants
  const rolledOpenRoles = useMemo(() => {
    const rolled: Record<string, number> = { ...openRolesByUnit };
    const childrenOf: Record<string, string[]> = {};
    for (const u of units) {
      if (u.parent_unit_id) (childrenOf[u.parent_unit_id] ||= []).push(u.id);
    }
    const sumDescendants = (id: string): number => {
      let total = openRolesByUnit[id] ?? 0;
      for (const cid of childrenOf[id] ?? []) total += sumDescendants(cid);
      rolled[id] = total;
      return total;
    };
    for (const u of units) if (!u.parent_unit_id) sumDescendants(u.id);
    return rolled;
  }, [units, openRolesByUnit]);

  const graphNodes: GraphNodeData[] = useMemo(
    () =>
      units.map((u) => {
        const openRoles = rolledOpenRoles[u.id] ?? 0;
        return {
          ...u,
          openRoles,
          pressure: pressureFor(openRoles),
        };
      }),
    [units, rolledOpenRoles],
  );

  const selectedUnit = useMemo(
    () => graphNodes.find((u) => u.id === selectedId) ?? graphNodes[0] ?? null,
    [graphNodes, selectedId],
  );

  const totalPeople = units.reduce((n, u) => n + u.member_count, 0);
  const totalUnits = units.length;

  const selectOptions = useMemo(() => flattenForSelect(units), [units]);

  async function doCreate(companyProfile: CompanyProfile | null) {
    const values = createForm.getValues();
    try {
      const newUnit = await createMutation.mutateAsync({
        name: values.name.trim(),
        unit_type: values.unit_type,
        parent_unit_id: values.parent_unit_id || null,
        company_profile: companyProfile,
      });
      createForm.reset({ name: "", unit_type: "division", parent_unit_id: "" });
      setShowCreate(false);
      setShowProfileDialog(false);
      router.push(`/settings/org-units/${newUnit.id}`);
    } catch (err) {
      if (applyApiErrorToForm(err, createForm)) throw err;
      setError(err instanceof Error ? err.message : "Failed to create unit");
      throw err;
    }
  }

  const onCreateSubmit = createForm.handleSubmit(async () => {
    if (createForm.getValues("unit_type") === "client_account") {
      setError("");
      setShowProfileDialog(true);
      return;
    }
    try {
      await doCreate(null);
    } catch {
      // error already surfaced via setError or form.setError
    }
  });

  async function handleClientAccountProfileSubmit(profile: CompanyProfile) {
    try {
      await doCreate(profile);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create unit");
      throw err;
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-[1600px] flex-col overflow-hidden">
      {/* Page header */}
      <div className="flex flex-shrink-0 items-end gap-4 px-8 pb-3.5 pt-6">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: "-0.5px", color: "var(--px-fg)" }}
          >
            Org structure
          </h1>
          <div
            className="mt-1 text-[13px]"
            style={{ color: "var(--px-fg-3)" }}
          >
            {loading
              ? "Loading…"
              : `${totalPeople} people across ${totalUnits} units · click a node to drill in`}
          </div>
        </div>
        <div className="flex-1" />
        <OrgLegend />
        {me?.is_super_admin && (
          <>
            <button
              type="button"
              onClick={() => setShowCreate((v) => !v)}
              className="px-btn primary sm"
            >
              {showCreate ? (
                <>
                  <IconX className="h-3 w-3" /> Cancel
                </>
              ) : (
                <>
                  <IconPlus className="h-3 w-3" /> New unit
                </>
              )}
            </button>
          </>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="mx-8 mb-3 flex items-start justify-between rounded-md border p-3 text-sm"
          style={{
            color: "var(--px-danger)",
            background: "var(--px-danger-bg)",
            borderColor: "var(--px-danger-line)",
          }}
        >
          <span>{error}</span>
          <button
            onClick={() => setError("")}
            className="ml-2 shrink-0 cursor-pointer"
            aria-label="Dismiss error"
          >
            <IconX className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Create form */}
      {showCreate && me?.is_super_admin && (
        <form
          onSubmit={onCreateSubmit}
          className="mx-8 mb-4 space-y-4 rounded-[10px] border p-5"
          style={{
            background: "var(--px-surface)",
            borderColor: "var(--px-hairline)",
          }}
        >
          <h2
            className="text-[11px] font-semibold uppercase"
            style={{ letterSpacing: "1.1px", color: "var(--px-fg-4)" }}
          >
            Create org unit
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="create-name" className="px-label">
                Name
              </label>
              <input
                id="create-name"
                type="text"
                className="px-input"
                placeholder="e.g., Engineering"
                {...createForm.register("name")}
              />
              {createForm.formState.errors.name && (
                <p className="px-hint" style={{ color: "var(--px-danger)" }}>
                  {createForm.formState.errors.name.message}
                </p>
              )}
            </div>
            <div>
              <label htmlFor="create-type" className="px-label">
                Type
              </label>
              <select
                id="create-type"
                className="px-input"
                {...createForm.register("unit_type")}
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
              <label htmlFor="create-parent" className="px-label">
                Parent unit{" "}
                <span
                  className="font-normal"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  (optional)
                </span>
              </label>
              <select
                id="create-parent"
                className="px-input"
                {...createForm.register("parent_unit_id")}
              >
                <option value="">None (top-level)</option>
                {selectOptions.map(({ unit: u, depth }) => (
                  <option key={u.id} value={u.id}>
                    {"  ".repeat(depth)}
                    {u.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="px-btn primary sm"
            >
              {createMutation.isPending ? "Creating…" : "Create unit"}
            </button>
          </div>
        </form>
      )}

      {/* Graph */}
      <div
        className="relative mx-8 flex-1 overflow-hidden rounded-[12px] border"
        style={{
          background: "var(--px-surface)",
          borderColor: "var(--px-hairline)",
          minHeight: 320,
        }}
      >
        {!loading && graphNodes.length === 0 ? (
          <div
            className="flex h-full flex-col items-center justify-center text-center"
          >
            <p className="text-sm" style={{ color: "var(--px-fg-3)" }}>
              No organizational units yet.
            </p>
            {me?.is_super_admin && (
              <button
                onClick={() => setShowCreate(true)}
                className="mt-3 cursor-pointer text-sm font-medium"
                style={{ color: "var(--px-accent)" }}
              >
                Create your first unit →
              </button>
            )}
          </div>
        ) : (
          <>
            <OrgGraph
              units={graphNodes}
              selectedId={selectedId}
              hoverId={hoverId}
              onSelect={setSelectedId}
              onHover={setHoverId}
              onOpen={(id) => router.push(`/settings/org-units/${id}`)}
            />
            <div
              className="absolute bottom-2.5 left-3.5 text-[10.5px]"
              style={{
                color: "var(--px-fg-4)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.3px",
              }}
            >
              click a node to focus · double-click to open detail
            </div>
          </>
        )}
      </div>

      {/* Detail panel */}
      {selectedUnit && (
        <div
          className="mx-8 my-3.5 grid grid-cols-[1.2fr_1fr_1fr] items-start gap-5 rounded-[10px] border p-5"
          style={{
            background: "var(--px-bg-2)",
            borderColor: "var(--px-hairline)",
          }}
        >
          <SelectedDetail unit={selectedUnit} onOpen={() => router.push(`/settings/org-units/${selectedUnit.id}`)} />
          <UnitMetrics unit={selectedUnit} />
          <UnitAccess unit={selectedUnit} />
        </div>
      )}

      {/* Client account profile dialog */}
      <Dialog
        open={showProfileDialog}
        onOpenChange={(open) => {
          if (createMutation.isPending) return;
          setShowProfileDialog(open);
        }}
      >
        <DialogContent widthClass="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Client account profile</DialogTitle>
            <DialogDescription>
              Set up the profile for{" "}
              <span className="font-medium" style={{ color: "var(--px-fg)" }}>
                {createForm.watch("name").trim()}
              </span>
              . This describes the end client — the company your recruiters are
              hiring <em>for</em>. It feeds the AI when generating JD
              enhancements and interview questions for this client&apos;s
              roles.
            </DialogDescription>
          </DialogHeader>
          <CompanyProfileForm
            onSubmit={handleClientAccountProfileSubmit}
            submitLabel="Create client account"
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ─── Detail panel subsections ─── */

function SelectedDetail({
  unit,
  onOpen,
}: {
  unit: GraphNodeData;
  onOpen: () => void;
}) {
  const pressureCopy: Record<Pressure, string> = {
    hot: "hiring hot",
    steady: "steady",
    cool: "cool",
  };
  const pressureColor: Record<Pressure, string> = {
    hot: "var(--px-accent)",
    steady: "var(--px-ok)",
    cool: "var(--px-fg-3)",
  };
  const pressureBg: Record<Pressure, string> = {
    hot: "var(--px-accent-tint)",
    steady: "var(--px-ok-bg)",
    cool: "var(--px-surface-2)",
  };
  const pressureLine: Record<Pressure, string> = {
    hot: "var(--px-accent-line)",
    steady: "var(--px-ok-line)",
    cool: "var(--px-hairline)",
  };

  return (
    <div>
      <div
        className="mb-1.5 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: "0.6px", color: "var(--px-fg-4)" }}
      >
        Selected unit
      </div>
      <div className="mb-1.5 flex items-baseline gap-2.5">
        <h2
          className="px-serif m-0 text-[26px] font-normal"
          style={{ letterSpacing: "-0.3px", color: "var(--px-fg)" }}
        >
          {unit.name}
        </h2>
        <span
          className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase"
          style={{
            letterSpacing: "0.3px",
            color: pressureColor[unit.pressure],
            background: pressureBg[unit.pressure],
            borderColor: pressureLine[unit.pressure],
          }}
        >
          {pressureCopy[unit.pressure]}
        </span>
      </div>
      <div
        className="text-[13px]"
        style={{ color: "var(--px-fg-3)", lineHeight: 1.55 }}
      >
        <span className="px-chip soft" style={{ height: 18, padding: "0 7px", fontSize: 10.5 }}>
          {unit.unit_type.replace("_", " ")}
        </span>{" "}
        {unit.member_count} people · {unit.openRoles} open role
        {unit.openRoles === 1 ? "" : "s"}.
      </div>
      <div className="mt-3.5 flex gap-2">
        <button onClick={onOpen} className="px-btn outline xs">
          Open detail →
        </button>
        <button className="px-btn ghost xs">
          <IconSearch className="h-3 w-3" /> Find person
        </button>
      </div>
    </div>
  );
}

function UnitMetrics({ unit }: { unit: GraphNodeData }) {
  const rows = [
    { l: "Headcount", v: String(unit.member_count), note: "all roles", ok: false },
    {
      l: "Open roles",
      v: String(unit.openRoles),
      note: unit.openRoles > 0 ? "in pipeline" : "—",
      ok: unit.openRoles > 0,
    },
    { l: "Time to hire", v: "—", note: "no data yet", ok: false },
    { l: "Offer accept", v: "—", note: "no data yet", ok: false },
  ];
  return (
    <div>
      <div
        className="mb-2 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: "0.6px", color: "var(--px-fg-4)" }}
      >
        Metrics
      </div>
      <div className="grid grid-cols-2 gap-2">
        {rows.map((r) => (
          <div
            key={r.l}
            className="rounded-lg border px-3 py-2.5"
            style={{
              background: "var(--px-surface)",
              borderColor: "var(--px-hairline)",
            }}
          >
            <div
              className="text-[10px] uppercase"
              style={{ letterSpacing: "0.3px", color: "var(--px-fg-4)" }}
            >
              {r.l}
            </div>
            <div
              className="px-mono mt-0.5 text-[20px] font-medium"
              style={{
                color: "var(--px-fg)",
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.1,
              }}
            >
              {r.v}
            </div>
            <div
              className="mt-1 text-[10.5px]"
              style={{ color: r.ok ? "var(--px-ok)" : "var(--px-fg-3)" }}
            >
              {r.note}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function UnitAccess({ unit }: { unit: GraphNodeData }) {
  const admins = unit.admin_emails.slice(0, 3);
  return (
    <div>
      <div
        className="mb-2 flex items-center text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: "0.6px", color: "var(--px-fg-4)" }}
      >
        <span>Access · {unit.name}</span>
        <span className="flex-1" />
      </div>
      <div
        className="overflow-hidden rounded-lg border"
        style={{
          background: "var(--px-surface)",
          borderColor: "var(--px-hairline)",
        }}
      >
        {admins.length === 0 ? (
          <div
            className="px-3 py-4 text-[12px]"
            style={{ color: "var(--px-fg-4)" }}
          >
            No admins assigned. Default falls back to super admin.
          </div>
        ) : (
          admins.map((email, i) => (
            <div
              key={email}
              className="grid items-center gap-2.5 px-3 py-2 text-[12px]"
              style={{
                gridTemplateColumns: "1fr auto",
                borderBottom:
                  i < admins.length - 1
                    ? "1px solid var(--px-hairline)"
                    : "none",
              }}
            >
              <div>
                <div
                  className="font-medium"
                  style={{ color: "var(--px-fg)" }}
                >
                  {email.split("@")[0]}
                </div>
                <div
                  className="px-mono text-[10.5px]"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  {email}
                </div>
              </div>
              <span
                className="px-chip"
                style={{
                  height: 18,
                  padding: "0 7px",
                  fontSize: 10,
                  background: "var(--px-accent-tint)",
                  color: "var(--px-accent)",
                  borderColor: "var(--px-accent-line)",
                  fontWeight: 600,
                }}
              >
                admin
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
