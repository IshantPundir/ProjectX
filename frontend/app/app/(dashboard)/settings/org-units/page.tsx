"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { jobsApi, type JobPostingSummary } from "@/lib/api/jobs";
import { useOrgUnits } from "@/lib/hooks/use-org-units";
import { useDeleteOrgUnit } from "@/lib/hooks/use-delete-org-unit";
import { isAnyAdmin, useMe } from "@/lib/hooks/use-me";
import { DangerConfirmDialog } from "@/components/px";
import { AccessDenied } from "@/components/dashboard/AccessDenied";
import type { UnitType } from "@/components/dashboard/org-units/unit-type-style";
import {
  OrgGraph,
  OrgLegend,
  type GraphNodeData,
  type Pressure,
} from "@/components/dashboard/org-units/OrgGraph";
import {
  OrgUnitCreateDialog,
  type CreateChildTarget,
} from "@/components/dashboard/org-units/OrgUnitCreateDialog";

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

/** Heuristic: map open-role count → hiring pressure. */
function pressureFor(openRoles: number): Pressure {
  if (openRoles >= 3) return "hot";
  if (openRoles > 0) return "steady";
  return "cool";
}

export default function OrgUnitsPage() {
  const router = useRouter();

  const meQuery = useMe();
  const unitsQuery = useOrgUnits();
  const jobsQuery = useQuery<JobPostingSummary[]>({
    queryKey: ["jobs-list"],
    queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
    staleTime: 10_000,
  });

  const units = useMemo(() => unitsQuery.data ?? [], [unitsQuery.data]);
  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data]);
  const loading = unitsQuery.isLoading;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const deleteMutation = useDeleteOrgUnit();

  // Spider-menu create flow: clicking a child-type pill opens a popup
  // dialog (single name input, with a profile-form cascade for client
  // accounts). After successful creation we navigate the user straight
  // to the new unit's detail page to finish configuring it.
  const [pickChildTarget, setPickChildTarget] =
    useState<CreateChildTarget | null>(null);

  function handlePickChild(parentId: string, childType: UnitType) {
    const parent = graphNodes.find((u) => u.id === parentId);
    if (!parent) return;
    setPickChildTarget({
      parentId,
      parentName: parent.name,
      childType,
    });
  }

  function handlePickChildCreated(newUnitId: string) {
    setPickChildTarget(null);
    toast.success("Unit created");
    router.push(`/settings/org-units/${newUnitId}`);
  }

  function handleDeleteRequest(id: string) {
    const unit = graphNodes.find((u) => u.id === id);
    if (!unit) return;
    setDeleteTarget({ id, name: unit.name });
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    try {
      await deleteMutation.mutateAsync(deleteTarget.id);
      if (selectedId === deleteTarget.id) setSelectedId(null);
      toast.success("Unit deleted");
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete unit");
    }
  }

  // Compute open-role count per unit (non-draft = active role). Skip
  // jobs with no org_unit_id — these are ATS-imported unlinked jobs that
  // surface on /jobs with a 'Not set up' chip; they don't belong to any
  // org unit yet.
  const openRolesByUnit = useMemo(() => {
    const m: Record<string, number> = {};
    for (const j of jobs) {
      if (j.status === "draft") continue;
      if (j.org_unit_id === null) continue;
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

  // RBAC: tenant admins only (super admin OR holds Admin on at least
  // one unit). Wait for /me to resolve before deciding to avoid a
  // flicker, then render the access-denied panel for non-admins. The
  // nav rail also hides this entry, so this is the second line of
  // defence against direct URL navigation.
  if (!meQuery.isLoading && !isAnyAdmin(meQuery.data)) {
    return <AccessDenied />;
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
      </div>

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
          </div>
        ) : (
          <>
            <OrgGraph
              units={graphNodes}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onOpen={(id) => router.push(`/settings/org-units/${id}`)}
              onDelete={handleDeleteRequest}
              onPickChild={handlePickChild}
            />
            <div
              className="absolute bottom-2.5 left-3.5 text-[10.5px]"
              style={{
                color: "var(--px-fg-4)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.3px",
              }}
            >
              click a node to focus · double-click to open detail · right-click to add a sub-unit
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

      <DangerConfirmDialog
        open={deleteTarget !== null}
        title={deleteTarget ? `Delete ${deleteTarget.name}?` : "Delete unit?"}
        description="This will permanently delete this unit. The unit must have no sub-units or members before it can be deleted. This cannot be undone."
        confirmLabel="Delete unit"
        pendingLabel="Deleting…"
        pending={deleteMutation.isPending}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />

      <OrgUnitCreateDialog
        target={pickChildTarget}
        onClose={() => setPickChildTarget(null)}
        onCreated={handlePickChildCreated}
      />
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
  const isLocked = !unit.is_accessible;
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
        {isLocked ? (
          <span
            className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase"
            style={{
              letterSpacing: "0.3px",
              color: "var(--px-fg-4)",
              background: "var(--px-surface-2)",
              borderColor: "var(--px-hairline)",
            }}
          >
            locked
          </span>
        ) : (
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
        )}
        {!isLocked &&
          unit.unit_type === "client_account" &&
          unit.company_profile_completion_status === "pending" && (
            <span
              title="Imported from ATS. Complete the company profile to enable job creation."
              className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase"
              style={{
                letterSpacing: "0.3px",
                color: "var(--px-caution, #b45309)",
                background: "var(--px-caution-bg, #fef3c7)",
                borderColor: "var(--px-caution-line, #fde68a)",
              }}
            >
              profile incomplete
            </span>
          )}
      </div>
      <div
        className="text-[13px]"
        style={{ color: "var(--px-fg-3)", lineHeight: 1.55 }}
      >
        <span className="px-chip soft" style={{ height: 18, padding: "0 7px", fontSize: 10.5 }}>
          {unit.unit_type.replace("_", " ")}
        </span>{" "}
        {isLocked ? (
          <>You don&apos;t have admin access on this unit.</>
        ) : (
          <>
            {unit.member_count} people · {unit.openRoles} open role
            {unit.openRoles === 1 ? "" : "s"}.
          </>
        )}
      </div>
      <div className="mt-3.5 flex gap-2">
        <button
          onClick={onOpen}
          className="px-btn outline xs"
          disabled={isLocked}
          title={isLocked ? "Locked — ask an admin for access" : undefined}
        >
          Open detail →
        </button>
        {!isLocked && (
          <button className="px-btn ghost xs">
            <IconSearch className="h-3 w-3" /> Find person
          </button>
        )}
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
