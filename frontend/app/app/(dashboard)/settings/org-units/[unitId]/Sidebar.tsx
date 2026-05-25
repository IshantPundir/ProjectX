"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { DangerConfirmDialog } from "@/components/px";
import { useDeleteOrgUnit } from "@/lib/hooks/use-delete-org-unit";
import type { OrgUnit } from "@/lib/api/org-units";
import { brand } from "@/lib/brand";

const TYPE_DOT: Record<string, string> = {
  company: "var(--px-accent)",
  client_account: "var(--px-human)",
  region: "var(--px-ai)",
  division: "var(--px-ok)",
  team: "var(--px-caution)",
};

export interface SidebarProps {
  unit: OrgUnit;
  /** Ancestor units, ordered root → ... → immediate parent. */
  parentChain: OrgUnit[];
  /** Direct children of `unit`. */
  subUnits: OrgUnit[];
  /** Optional card slotted at the very top — used by Division / Region /
   *  Company / Client to surface Direct members in the sidebar. */
  topCard?: React.ReactNode;
  /** Optional card slotted between hierarchy and governance — used by
   *  Team to surface Open jobs in the sidebar. */
  middleCard?: React.ReactNode;
}

/**
 * Sidebar renders the design's exact card stack:
 *   1. Optional top card (Direct members on Division/Region/Company/Client,
 *      Open jobs on Team)
 *   2. Hierarchy
 *   3. Governance (or Tenant info on Company root)
 *   4. Delete <type> button (suppressed on root)
 */
export function Sidebar({
  unit,
  parentChain,
  subUnits,
  topCard,
  middleCard,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      {topCard}
      {middleCard}
      <div className="sidebar-card">
        <div className="sidebar-card-title">Hierarchy</div>
        <HierarchyTree
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
        />
      </div>
      {unit.is_root ? (
        <TenantInfoCard unit={unit} />
      ) : (
        <GovernanceCard unit={unit} />
      )}
      {!unit.is_root && <DeleteUnitButton unit={unit} />}
    </aside>
  );
}

function HierarchyTree({
  unit,
  parentChain,
  subUnits,
}: {
  unit: OrgUnit;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
}) {
  return (
    <div className="hierarchy-tree">
      {parentChain.map((ancestor, i) => (
        <Link
          key={ancestor.id}
          href={`/settings/org-units/${ancestor.id}`}
          className="hierarchy-node"
          style={i > 0 ? { paddingLeft: 8 + (i - 1) * 14 } : undefined}
        >
          {i > 0 && (
            <span
              className="conn"
              style={{ paddingLeft: (i - 1) * 14 }}
            >
              └─
            </span>
          )}
          <span
            className="type-dot"
            style={{ background: TYPE_DOT[ancestor.unit_type] ?? "var(--px-fg-4)" }}
          />
          <span>{ancestor.name}</span>
        </Link>
      ))}
      <div className="hierarchy-node current">
        {parentChain.length > 0 && (
          <span
            className="conn"
            style={{ paddingLeft: (parentChain.length - 1) * 14 }}
          >
            └─
          </span>
        )}
        <span
          className="type-dot"
          style={{ background: TYPE_DOT[unit.unit_type] ?? "var(--px-fg-4)" }}
        />
        <span>{unit.name}</span>
      </div>
      {subUnits.slice(0, 8).map((child) => (
        <Link
          key={child.id}
          href={`/settings/org-units/${child.id}`}
          className="hierarchy-node"
          style={{ opacity: 0.7 }}
        >
          <span
            className="conn"
            style={{ paddingLeft: parentChain.length * 14 }}
          >
            └─
          </span>
          <span
            className="type-dot"
            style={{ background: TYPE_DOT[child.unit_type] ?? "var(--px-fg-4)" }}
          />
          <span>{child.name}</span>
        </Link>
      ))}
    </div>
  );
}

function GovernanceCard({ unit }: { unit: OrgUnit }) {
  return (
    <div className="sidebar-card">
      <div className="sidebar-card-title">Governance</div>
      <div className="gov-list">
        {unit.created_by_email && (
          <GovItem label="Created by" value={unit.created_by_email} />
        )}
        <GovItem
          label="Created at"
          value={formatDate(unit.created_at)}
          mono
        />
        <GovItem
          label="Deletable by"
          value={unit.deletable_by_email ?? "Super admin"}
        />
        <GovItem
          label="Admin delete disabled"
          value={unit.admin_delete_disabled ? "Yes" : "No"}
        />
      </div>
    </div>
  );
}

function TenantInfoCard({ unit }: { unit: OrgUnit }) {
  return (
    <div className="sidebar-card">
      <div className="sidebar-card-title">Tenant info</div>
      <div className="gov-list">
        <GovItem
          label="Created at"
          value={formatDate(unit.created_at)}
          mono
        />
        <GovItem label="Tenant ID" value={unit.client_id} mono />
      </div>
      <div className="tenant-note">
        The root company unit cannot be deleted. To remove the tenant
        entirely, contact {brand.shortName} support.
      </div>
    </div>
  );
}

function GovItem({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="gov-item">
      <span className="gov-label">{label}</span>
      <span className={`gov-value${mono ? " mono" : ""}`}>{value}</span>
    </div>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

function DeleteUnitButton({ unit }: { unit: OrgUnit }) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const deleteMutation = useDeleteOrgUnit();
  const label = `Delete ${prettyType(unit.unit_type).toLowerCase()}`;

  async function handleConfirm() {
    try {
      await deleteMutation.mutateAsync(unit.id);
      toast.success(`${prettyType(unit.unit_type)} deleted`);
      router.push("/settings/org-units");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete unit");
    }
  }

  return (
    <>
      <button
        className="btn destructive full"
        type="button"
        onClick={() => setOpen(true)}
      >
        Delete {prettyType(unit.unit_type).toLowerCase()}
      </button>
      <DangerConfirmDialog
        open={open}
        title={label}
        description={
          <>
            Permanently delete <strong>{unit.name}</strong>? This cannot be
            undone. The unit must have no sub-units and no members.
          </>
        }
        confirmLabel={label}
        pendingLabel="Deleting…"
        pending={deleteMutation.isPending}
        onConfirm={handleConfirm}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

function prettyType(type: string): string {
  switch (type) {
    case "company":
      return "Company";
    case "client_account":
      return "Client";
    case "region":
      return "Region";
    case "division":
      return "Division";
    case "team":
      return "Team";
    default:
      return "Unit";
  }
}
