"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";

import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { orgUnitsApi, type OrgUnit } from "@/lib/api/org-units";
import { jobsApi, type JobPostingSummary } from "@/lib/api/jobs";

import { CompanyProfileDetail } from "./CompanyProfileDetail";
import { DivisionDetail } from "./DivisionDetail";
import { RegionDetail } from "./RegionDetail";
import { TeamDetail } from "./TeamDetail";

export default function OrgUnitDetailPage() {
  const params = useParams<{ unitId: string }>();
  const router = useRouter();
  const unitId = params.unitId;

  const [unit, setUnit] = useState<OrgUnit | null>(null);
  const [allUnits, setAllUnits] = useState<OrgUnit[]>([]);
  const [jobs, setJobs] = useState<JobPostingSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const token = await getFreshSupabaseToken();
      const [current, listed, jobsList] = await Promise.all([
        orgUnitsApi.get(token, unitId),
        orgUnitsApi.list(token),
        jobsApi.list(token).catch(() => [] as JobPostingSummary[]),
      ]);
      setUnit(current);
      setAllUnits(listed);
      setJobs(jobsList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [unitId]);

  useEffect(() => {
    load();
  }, [load]);

  // Ancestry (crumb path for the header)
  const parentPath = useMemo(() => {
    if (!unit) return "";
    const byId = new Map(allUnits.map((u) => [u.id, u]));
    const chain: string[] = [];
    let cur = unit.parent_unit_id ? byId.get(unit.parent_unit_id) : null;
    while (cur) {
      chain.unshift(cur.name);
      cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : null;
    }
    return chain.join(" · ");
  }, [unit, allUnits]);

  // Direct children
  const subUnits = useMemo(() => {
    if (!unit) return [];
    return allUnits.filter((u) => u.parent_unit_id === unit.id);
  }, [unit, allUnits]);

  // Open roles per unit (non-draft). Rolled up for non-leaf units.
  const { openRolesCount, openRolesByChildId } = useMemo(() => {
    const raw: Record<string, number> = {};
    for (const j of jobs) {
      if (j.status === "draft") continue;
      raw[j.org_unit_id] = (raw[j.org_unit_id] ?? 0) + 1;
    }
    const childrenOf: Record<string, string[]> = {};
    for (const u of allUnits) {
      if (u.parent_unit_id) (childrenOf[u.parent_unit_id] ||= []).push(u.id);
    }
    const rolled = (id: string): number => {
      let total = raw[id] ?? 0;
      for (const cid of childrenOf[id] ?? []) total += rolled(cid);
      return total;
    };
    const byChild: Record<string, number> = {};
    if (unit) {
      for (const c of childrenOf[unit.id] ?? []) byChild[c] = rolled(c);
    }
    return {
      openRolesCount: unit ? rolled(unit.id) : 0,
      openRolesByChildId: byChild,
    };
  }, [jobs, allUnits, unit]);

  const handleSaved = useCallback((updated: OrgUnit) => {
    setUnit(updated);
    setAllUnits((prev) =>
      prev.map((u) => (u.id === updated.id ? updated : u)),
    );
  }, []);

  const onBack = () => router.push("/settings/org-units");

  if (loading) {
    return (
      <div className="mx-auto max-w-[1200px] px-8 pt-6 text-sm" style={{ color: "var(--px-fg-3)" }}>
        Loading unit…
      </div>
    );
  }

  if (error || !unit) {
    return (
      <div className="mx-auto max-w-[1200px] px-8 pt-6">
        <div
          className="rounded-md border p-4 text-sm"
          style={{
            color: "var(--px-danger)",
            background: "var(--px-danger-bg)",
            borderColor: "var(--px-danger-line)",
          }}
        >
          {error || "Unit not found"}
        </div>
      </div>
    );
  }

  // Route by unit_type
  if (unit.unit_type === "company" || unit.unit_type === "client_account") {
    return (
      <div className="mx-auto max-w-[1200px]">
        <CompanyProfileDetail
          unit={unit}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={(u) => {
            handleSaved(u);
            toast.success("Changes saved");
          }}
          openRolesCount={openRolesCount}
        />
      </div>
    );
  }
  if (unit.unit_type === "region") {
    return (
      <div className="mx-auto max-w-[1200px]">
        <RegionDetail
          unit={unit}
          parentPath={parentPath}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={handleSaved}
          openRolesCount={openRolesCount}
        />
      </div>
    );
  }
  if (unit.unit_type === "division") {
    return (
      <div className="mx-auto max-w-[1200px]">
        <DivisionDetail
          unit={unit}
          parentPath={parentPath}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={handleSaved}
          openRolesCount={openRolesCount}
          openRolesByChildId={openRolesByChildId}
        />
      </div>
    );
  }
  // team (or any other leaf-like type)
  return (
    <div className="mx-auto max-w-[1200px]">
      <TeamDetail
        unit={unit}
        parentPath={parentPath}
        onBack={onBack}
        onSaved={handleSaved}
        openRolesCount={openRolesCount}
      />
    </div>
  );
}
