"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import { type OrgUnit } from "@/lib/api/org-units";
import { canManageUnit, useMe } from "@/lib/hooks/use-me";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { usePipelineTemplates } from "@/lib/hooks/use-pipeline-templates";

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
  companyFormSchema,
  type CompanyFormValues,
} from "./schema";

import "./detail.css";

export interface CompanyDetailProps {
  unit: OrgUnit;
  isClientAccount: boolean;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
  openRolesCount: number;
  openRolesByChildId: Record<string, number>;
  onBack: () => void;
  onSaved: (next: OrgUnit) => void;
}

/**
 * Company root + Client account detail page — 1:1 with the design HTML's
 * #page-company and #page-client_account blocks. Single component with
 * an `isClientAccount` flag controlling:
 *   - type pill ("Company · Root" vs "Client account")
 *   - presence of breadcrumb
 *   - Address block inheritance source label
 *   - Sidebar bottom card ("Tenant info" vs "Governance")
 *   - Delete button (suppressed on root)
 */
export function CompanyDetail({
  unit,
  isClientAccount,
  parentChain,
  subUnits,
  openRolesCount,
  openRolesByChildId,
  onBack,
  onSaved,
}: CompanyDetailProps) {
  const [mode, setMode] = React.useState<"view" | "edit">("view");
  const isEdit = mode === "edit";

  const defaults = React.useMemo<CompanyFormValues>(
    () => ({
      name: unit.name,
      about: unit.about ?? "",
      industry: unit.industry ?? "",
      hiring_bar: unit.hiring_bar ?? "",
      website: unit.website ?? "",
      country: unit.country ?? "",
      state: unit.state ?? "",
      city: unit.city ?? "",
    }),
    [
      unit.name,
      unit.about,
      unit.industry,
      unit.hiring_bar,
      unit.website,
      unit.country,
      unit.state,
      unit.city,
    ],
  );

  const form = useForm<CompanyFormValues>({
    resolver: zodResolver(companyFormSchema),
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

  const inheritedFromName = React.useMemo(() => {
    if (!isClientAccount) return null;
    const sourceId = unit.inherited_address?.source_unit_id ?? null;
    if (!sourceId || sourceId === unit.id) return null;
    return parentChain.find((u) => u.id === sourceId)?.name ?? null;
  }, [
    isClientAccount,
    unit.id,
    unit.inherited_address?.source_unit_id,
    parentChain,
  ]);

  async function onSubmit(values: CompanyFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          about: values.about, set_about: true,
          industry: values.industry, set_industry: true,
          hiring_bar: values.hiring_bar, set_hiring_bar: true,
          website: values.website, set_website: true,
          country: values.country, set_country: true,
          state: values.state, set_state: true,
          city: values.city, set_city: true,
        },
      });
      onSaved(updated);
      toast.success(isClientAccount ? "Client account saved" : "Company saved");
      setMode("view");
      form.reset({
        name: updated.name,
        about: updated.about ?? "",
        industry: updated.industry ?? "",
        hiring_bar: updated.hiring_bar ?? "",
        website: updated.website ?? "",
        country: updated.country ?? "",
        state: updated.state ?? "",
        city: updated.city ?? "",
      });
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  }

  function handleDiscard() {
    form.reset(defaults);
    setMode("view");
  }

  const crumbs = isClientAccount
    ? parentChain.map((u) => ({
        label: u.name,
        href: `/settings/org-units/${u.id}`,
      }))
    : [];

  // Stats roll-up: prefer subUnit type counts that match the design
  // ("3 regions · 12 divisions · 4 direct members · 42 open jobs").
  const regionCount = subUnits.filter((u) => u.unit_type === "region").length;
  const divisionCount = subUnits.filter(
    (u) => u.unit_type === "division",
  ).length;

  return (
    <main
      className="org-unit-detail-root"
      data-edit-mode={isEdit ? "true" : "false"}
    >
      <header className="unit-header">
        {isClientAccount && <CrumbBack onBack={onBack} />}
        <div className="unit-header-row">
          <div className="unit-header-main">
            <div className="unit-pills">
              <UnitPill
                type={isClientAccount ? "client_account" : "company"}
                label={isClientAccount ? "Client account" : "Company · Root"}
              />
            </div>
            {isClientAccount && <UnitCrumb items={crumbs} />}
            <h1
              className="unit-name"
              data-editable-text={isClientAccount ? "client-name" : "company-name"}
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
            <div className="unit-website">
              <span className="unit-website-label">Website</span>
              <input
                className="input mono unit-website-input"
                aria-label="Website"
                placeholder="https://example.com"
                {...form.register("website")}
              />
            </div>
            <div className="unit-industry" data-testid="unit-industry-row">
              <span className="unit-industry-label">Industry</span>
              <input
                className="input unit-industry-input"
                aria-label="Industry"
                placeholder="e.g. Banking / Financial Services"
                {...form.register("industry")}
              />
            </div>
            <div className="unit-about">
              <span className="unit-about-label">About</span>
              <textarea
                className="textarea unit-about-body"
                rows={4}
                aria-label="About"
                placeholder="Describe what this client builds in 1-2 sentences."
                {...form.register("about")}
              />
            </div>
            <div className="address-block" aria-label="Address">
              <AddressChip
                label="Country"
                isEdit={isEdit}
                value={watched.country}
                inheritedValue={
                  unit.inherited_address?.values.country ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("country", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="State"
                isEdit={isEdit}
                value={watched.state}
                inheritedValue={
                  unit.inherited_address?.values.state ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("state", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="City"
                isEdit={isEdit}
                value={watched.city}
                inheritedValue={
                  unit.inherited_address?.values.city ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("city", v ?? "", { shouldDirty: true })
                }
              />
            </div>
            <div className="unit-stats">
              {regionCount > 0 && (
                <>
                  <StatItem
                    value={regionCount}
                    label={regionCount === 1 ? "region" : "regions"}
                  />
                  <StatSep />
                </>
              )}
              {divisionCount > 0 && (
                <>
                  <StatItem
                    value={divisionCount}
                    label={divisionCount === 1 ? "division" : "divisions"}
                  />
                  <StatSep />
                </>
              )}
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
          {/* Hiring bar (highlighted) */}
          <section className="section highlight">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">Hiring bar</div>
                <div className="section-sub">
                  {isClientAccount
                    ? "Source of truth for jobs anchored under this client account."
                    : "Source of truth for the tenant. Inherited by every sub-unit unless a Client account overrides."}
                </div>
              </div>
            </div>
            <div className="card">
              <div className="profile-narrative">
                <span className="profile-narrative-label">
                  Hiring bar narrative
                </span>
                <textarea
                  className="textarea profile-narrative-body"
                  rows={5}
                  aria-label="Hiring bar narrative"
                  placeholder="Describe the bar. Read verbatim by Copilot when grounding JDs."
                  {...form.register("hiring_bar")}
                />
              </div>
              <div className="profile-action-row">
                <span className="profile-updated">
                  {unit.company_profile_completed_at
                    ? `Last updated ${unit.company_profile_completed_at.slice(0, 10)}`
                    : "Profile not yet complete"}
                </span>
              </div>
            </div>
          </section>

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
              <div className="empty-state">
                No sub-units yet. Add a region or division from the org
                graph.
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
                    {isClientAccount ? " · owned by this client" : ""}
                  </span>
                </div>
                <div className="section-sub">
                  {isClientAccount
                    ? "Defaults for divisions under this client that don't have their own."
                    : "Defaults for divisions that don't have their own. Each division can also define its own templates."}
                </div>
              </div>
              <a
                className="btn outline xs"
                href={`/settings/org-units/${unit.id}/pipeline-templates`}
              >
                + Manage{isClientAccount ? "" : " tenant"} templates →
              </a>
            </div>
            {templatesQuery.isLoading ? (
              <div className="empty-state">Loading templates…</div>
            ) : templates.length === 0 ? (
              <div className="empty-state">
                No tenant templates yet.
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
              helperText={
                isClientAccount
                  ? "Client-account admins live here. Per-member role picker."
                  : "Tenant-level admins live here. Per-member role picker."
              }
            />
          }
        />
      </div>
    </main>
  );
}
