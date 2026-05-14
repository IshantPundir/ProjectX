"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import { type OrgUnit } from "@/lib/api/org-units";
import { canManageUnit, useMe } from "@/lib/hooks/use-me";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";

import { Sidebar } from "./Sidebar";
import { SidebarMembersCard } from "./SidebarMembersCard";
import {
  AddressChip,
  CrumbBack,
  HeaderActions,
  StatItem,
  StatSep,
  SubUnitCard,
  UnitCrumb,
  UnitPill,
} from "./shared";
import {
  regionFormSchema,
  type RegionFormValues,
} from "./schema";

import "./detail.css";

export interface RegionDetailProps {
  unit: OrgUnit;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
  openRolesCount: number;
  openRolesByChildId: Record<string, number>;
  onBack: () => void;
  onSaved: (next: OrgUnit) => void;
}

export function RegionDetail({
  unit,
  parentChain,
  subUnits,
  openRolesCount,
  openRolesByChildId,
  onBack,
  onSaved,
}: RegionDetailProps) {
  const [mode, setMode] = React.useState<"view" | "edit">("view");

  const defaults = React.useMemo<RegionFormValues>(
    () => ({
      name: unit.name,
      country: unit.country ?? "",
      state: unit.state ?? "",
      city: unit.city ?? "",
    }),
    [unit.name, unit.country, unit.state, unit.city],
  );

  const form = useForm<RegionFormValues>({
    resolver: zodResolver(regionFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    form.reset(defaults);
  }, [defaults, form]);

  const updateMutation = useUpdateOrgUnit();
  const meQuery = useMe();
  const canManageMembers = canManageUnit(meQuery.data, unit.id);
  const watched = form.watch();
  const isEdit = mode === "edit";

  const inheritedFromName = React.useMemo(() => {
    const sourceId = unit.inherited_address?.source_unit_id ?? null;
    if (!sourceId || sourceId === unit.id) return null;
    return parentChain.find((u) => u.id === sourceId)?.name ?? null;
  }, [unit.id, unit.inherited_address?.source_unit_id, parentChain]);

  async function onSubmit(values: RegionFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim(),
          country: values.country, set_country: true,
          state: values.state, set_state: true,
          city: values.city, set_city: true,
        },
      });
      onSaved(updated);
      toast.success("Region saved");
      setMode("view");
      form.reset({
        name: updated.name,
        country: updated.country ?? "",
        state: updated.state ?? "",
        city: updated.city ?? "",
      });
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save region");
    }
  }

  function handleDiscard() {
    form.reset(defaults);
    setMode("view");
  }

  const crumbs = parentChain.map((u) => ({
    label: u.name,
    href: `/settings/org-units/${u.id}`,
  }));

  return (
    <main
      className="org-unit-detail-root"
      data-edit-mode={isEdit ? "true" : "false"}
    >
      <header className="unit-header">
        <CrumbBack onBack={onBack} />
        <div className="unit-header-row">
          <div className="unit-header-main">
            <div className="unit-pills">
              <UnitPill type="region" />
            </div>
            <UnitCrumb items={crumbs} />
            <h1
              className="unit-name"
              style={{ marginTop: 8 }}
              data-editable-text="region-name"
              contentEditable={isEdit}
              suppressContentEditableWarning
              onBlur={(e) => {
                const next = e.currentTarget.textContent?.trim() ?? "";
                if (next && next !== watched.name) {
                  form.setValue("name", next, { shouldDirty: true });
                }
              }}
            >
              {unit.name}
            </h1>
            <div className="address-block" aria-label="Address">
              <AddressChip
                label="Country"
                isEdit={isEdit}
                value={watched.country}
                inheritedValue={unit.inherited_address?.values.country ?? null}
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("country", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="State"
                isEdit={isEdit}
                value={watched.state}
                inheritedValue={unit.inherited_address?.values.state ?? null}
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("state", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="City"
                isEdit={isEdit}
                value={watched.city}
                inheritedValue={unit.inherited_address?.values.city ?? null}
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("city", v ?? "", { shouldDirty: true })
                }
              />
            </div>
            <div className="unit-stats">
              <StatItem
                value={subUnits.filter((u) => u.unit_type === "division").length}
                label={
                  subUnits.filter((u) => u.unit_type === "division").length === 1
                    ? "division"
                    : "divisions"
                }
              />
              <StatSep />
              <StatItem value={unit.member_count} label="direct members" />
              <StatSep />
              <StatItem value={openRolesCount} label="open jobs" rolledUp />
            </div>
          </div>
          <HeaderActions
            mode={mode}
            onModeChange={setMode}
            saving={updateMutation.isPending}
            dirty={form.formState.isDirty}
            onSave={form.handleSubmit(onSubmit)}
            onDiscard={handleDiscard}
          />
        </div>
      </header>

      <div className="unit-body">
        <div>
          {/* Sub-units */}
          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">
                  Sub-units <span className="count">{subUnits.length}</span>
                </div>
              </div>
              <a
                className="btn outline xs"
                href={`/settings/org-units?parent=${unit.id}`}
              >
                + New sub-unit
              </a>
            </div>
            {subUnits.length === 0 ? (
              <div className="empty-state">No sub-units yet.</div>
            ) : (
              <div className="subunits-grid">
                {subUnits.map((child) => (
                  <SubUnitCard
                    key={child.id}
                    unit={child}
                    href={`/settings/org-units/${child.id}`}
                    openRoles={openRolesByChildId[child.id] ?? 0}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <Sidebar
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          topCard={
            <SidebarMembersCard
              unitId={unit.id}
              canManageMembers={canManageMembers}
              helperText="Often empty — most members live at division/team level. Useful for regional HR partners and legal."
            />
          }
        />
      </div>
    </main>
  );
}
