"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import {
  type DivisionMetadata,
  type OrgUnit,
} from "@/lib/api/org-units";
import { canManageUnit, useMe } from "@/lib/hooks/use-me";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { usePipelineTemplates } from "@/lib/hooks/use-pipeline-templates";

import { Sidebar } from "./Sidebar";
import { SidebarMembersCard } from "./SidebarMembersCard";
import {
  CrumbBack,
  HeaderActions,
  StatItem,
  StatSep,
  SubUnitCard,
  UnitCrumb,
  UnitPill,
} from "./shared";
import {
  divisionFormSchema,
  mergeMetadata,
  type DivisionFormValues,
} from "./schema";

import "./detail.css";

export interface DivisionDetailProps {
  unit: OrgUnit;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
  openRolesCount: number;
  openRolesByChildId: Record<string, number>;
  onBack: () => void;
  onSaved: (next: OrgUnit) => void;
}

/**
 * Division detail page — 1:1 with the design HTML's #page-division block.
 */
export function DivisionDetail({
  unit,
  parentChain,
  subUnits,
  openRolesCount,
  openRolesByChildId,
  onBack,
  onSaved,
}: DivisionDetailProps) {
  const metadata = (unit.metadata ?? {}) as DivisionMetadata;
  const [mode, setMode] = React.useState<"view" | "edit">("view");

  const defaults = React.useMemo<DivisionFormValues>(
    () => ({
      name: unit.name,
      description: metadata.description ?? "",
    }),
    [unit.name, metadata.description],
  );

  const form = useForm<DivisionFormValues>({
    resolver: zodResolver(divisionFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    form.reset(defaults);
  }, [defaults, form]);

  const updateMutation = useUpdateOrgUnit();
  const meQuery = useMe();
  const canManageMembers = canManageUnit(meQuery.data, unit.id);
  const templatesQuery = usePipelineTemplates(unit.id);
  const templates = templatesQuery.data ?? [];
  const watched = form.watch();

  async function onSubmit(values: DivisionFormValues) {
    try {
      const merged = mergeMetadata(unit.metadata, {
        description: values.description?.trim() || undefined,
      });
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          metadata: merged,
          set_metadata: true,
        },
      });
      onSaved(updated);
      toast.success("Division saved");
      setMode("view");
      form.reset({
        name: updated.name,
        description:
          (updated.metadata as DivisionMetadata | null)?.description ?? "",
      });
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(
        err instanceof Error ? err.message : "Failed to save division",
      );
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
      data-edit-mode={mode === "edit" ? "true" : "false"}
    >
      <header className="unit-header">
        <CrumbBack onBack={onBack} />
        <div className="unit-header-row">
          <div className="unit-header-main">
            <div className="unit-pills">
              <UnitPill type="division" />
            </div>
            <UnitCrumb items={crumbs} />
            <h1
              className="unit-name"
              style={{ marginTop: 8 }}
              data-editable-text="division-name"
              contentEditable={mode === "edit"}
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
            <p
              className="unit-description"
              data-editable-text="division-description"
              contentEditable={mode === "edit"}
              suppressContentEditableWarning
              onBlur={(e) => {
                const next = e.currentTarget.textContent?.trim() ?? "";
                if (next !== watched.description) {
                  form.setValue("description", next, { shouldDirty: true });
                }
              }}
            >
              {watched.description ||
                (mode === "edit"
                  ? "Click to add a description. Copilot uses this when enriching JDs anchored to teams under this division."
                  : "")}
            </p>
            <div className="unit-stats">
              <StatItem
                value={subUnits.filter((u) => u.unit_type === "team").length}
                label={
                  subUnits.filter((u) => u.unit_type === "team").length === 1
                    ? "team"
                    : "teams"
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
                  Sub-units{" "}
                  <span className="count">{subUnits.length}</span>
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
              <div className="empty-state">
                No sub-units yet. Add a team from the org graph.
              </div>
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

          {/* Pipeline templates */}
          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">
                  Pipeline templates{" "}
                  <span className="count">
                    {templates.length}{" "}
                    {templates.length === 1 ? "template" : "templates"}
                  </span>
                </div>
                <div className="section-sub">
                  Reusable interview pipelines for jobs anchored under any
                  team in this division. The default template auto-applies
                  when creating a new JD.
                </div>
              </div>
              <a
                className="btn outline xs"
                href={`/settings/org-units/${unit.id}/pipeline-templates`}
              >
                + Manage templates →
              </a>
            </div>
            {templatesQuery.isLoading ? (
              <div className="empty-state">Loading templates…</div>
            ) : templates.length === 0 ? (
              <div className="empty-state">
                No templates yet. Inherits the tenant default.
              </div>
            ) : (
              <div className="card">
                {[...templates]
                  .sort((a, b) =>
                    a.is_default === b.is_default ? 0 : a.is_default ? -1 : 1,
                  )
                  .map((tpl) => {
                    const stages = [...tpl.stages].sort(
                      (a, b) => a.position - b.position,
                    );
                    return (
                      <div key={tpl.id} className="template-row">
                        <div className="template-name">
                          {tpl.name}
                          {tpl.is_default && (
                            <span className="default-tag">Default</span>
                          )}
                        </div>
                        <div className="template-stages">
                          {stages.map((s, i) => (
                            <React.Fragment key={s.id}>
                              {i > 0 && (
                                <span className="arrow" aria-hidden="true">
                                  →
                                </span>
                              )}
                              <span className="stage">{s.name}</span>
                            </React.Fragment>
                          ))}
                        </div>
                        <a
                          className="btn link"
                          href={`/settings/org-units/${unit.id}/pipeline-templates/${tpl.id}`}
                        >
                          Edit
                        </a>
                      </div>
                    );
                  })}
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
              helperText="Per-member role picker. Default-role logic only applies to teams."
            />
          }
        />
      </div>
    </main>
  );
}
