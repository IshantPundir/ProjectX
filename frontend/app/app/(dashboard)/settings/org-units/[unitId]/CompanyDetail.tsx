"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import {
  type CompanyMetadata,
  type OrgUnit,
} from "@/lib/api/org-units";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { usePipelineTemplates } from "@/lib/hooks/use-pipeline-templates";
import { type CompanyProfile } from "@/components/dashboard/company-profile-form";

import { Sidebar } from "./Sidebar";
import { SidebarMembersCard } from "./SidebarMembersCard";
import {
  COMPLIANCE_FLAGS,
  ComplianceRow,
  CrumbBack,
  CURRENCY_COMMON_VALUES,
  CURRENCY_OPTIONS,
  HeaderActions,
  LOCALE_OPTIONS,
  LocaleChip,
  StatItem,
  StatSep,
  SubUnitCard,
  TIMEZONE_OPTIONS,
  UnitCrumb,
  UnitPill,
  getLocaleCommonValues,
  getTimezoneCommonValues,
  localeDefaults,
} from "./shared";
import {
  companyFormSchema,
  mergeMetadata,
  type CompanyFormValues,
} from "./schema";

import "./detail.css";

const ABOUT_MIN = 30;
const ABOUT_MAX = 1500;
const HIRING_BAR_MIN = 20;
const HIRING_BAR_MAX = 1500;

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
 *   - LocaleStrip variant ("source" vs "inherit")
 *   - Compliance flags variant ("source" vs "inherit")
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
  const metadata = (unit.metadata ?? {}) as CompanyMetadata;
  const profile = unit.company_profile;
  const [mode, setMode] = React.useState<"view" | "edit">("view");
  const isEdit = mode === "edit";

  const defaults = React.useMemo<CompanyFormValues>(
    () => ({
      name: unit.name,
      short_name: metadata.short_name ?? "",
      website: metadata.website ?? "",
      about: profile?.about ?? "",
      hiring_bar: profile?.hiring_bar ?? "",
      default_timezone: metadata.default_timezone,
      default_currency: metadata.default_currency,
      default_locale: metadata.default_locale,
      compliance_aivia_il: metadata.compliance_aivia_il,
      compliance_gdpr_eu: metadata.compliance_gdpr_eu,
      compliance_ccpa_ca: metadata.compliance_ccpa_ca,
    }),
    [
      unit.name,
      metadata.short_name,
      metadata.website,
      metadata.default_timezone,
      metadata.default_currency,
      metadata.default_locale,
      metadata.compliance_aivia_il,
      metadata.compliance_gdpr_eu,
      metadata.compliance_ccpa_ca,
      profile?.about,
      profile?.hiring_bar,
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
  const templatesQuery = usePipelineTemplates(unit.id);
  const templates = templatesQuery.data ?? [];
  const watched = form.watch();

  const localeVariant = isClientAccount ? "inherit" : "source";
  const complianceVariant = isClientAccount ? "inherit" : "source";

  const inheritedFromName = React.useMemo(() => {
    if (!isClientAccount) return null;
    const sourceId =
      unit.inherited_locale?.source_unit_id ??
      unit.inherited_compliance?.source_unit_id ??
      null;
    if (!sourceId || sourceId === unit.id) return null;
    return parentChain.find((u) => u.id === sourceId)?.name ?? null;
  }, [
    isClientAccount,
    unit.id,
    unit.inherited_locale?.source_unit_id,
    unit.inherited_compliance?.source_unit_id,
    parentChain,
  ]);

  function buildProfilePayload(values: CompanyFormValues): {
    profile: CompanyProfile | null;
    canPersist: boolean;
  } {
    const about = values.about?.trim() ?? "";
    const hiringBar = values.hiring_bar?.trim() ?? "";
    const industry = profile?.industry;
    const companyStage = profile?.company_stage;
    const canPersist =
      about.length >= ABOUT_MIN &&
      about.length <= ABOUT_MAX &&
      hiringBar.length >= HIRING_BAR_MIN &&
      hiringBar.length <= HIRING_BAR_MAX &&
      !!industry &&
      !!companyStage;
    if (!canPersist) {
      return { profile: profile ?? null, canPersist: false };
    }
    return {
      profile: {
        about,
        industry: industry as CompanyProfile["industry"],
        company_stage: companyStage as CompanyProfile["company_stage"],
        hiring_bar: hiringBar,
      },
      canPersist: true,
    };
  }

  async function onSubmit(values: CompanyFormValues) {
    try {
      const merged = mergeMetadata(unit.metadata, {
        short_name: values.short_name?.trim() || undefined,
        website: values.website?.trim() || undefined,
        default_timezone: values.default_timezone,
        default_currency: values.default_currency,
        default_locale: values.default_locale,
        compliance_aivia_il: values.compliance_aivia_il,
        compliance_gdpr_eu: values.compliance_gdpr_eu,
        compliance_ccpa_ca: values.compliance_ccpa_ca,
      });
      const profilePayload = buildProfilePayload(values);
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          metadata: merged,
          set_metadata: true,
          ...(profilePayload.canPersist
            ? {
                company_profile: profilePayload.profile,
                set_company_profile: true,
              }
            : {}),
        },
      });
      onSaved(updated);
      toast.success(
        profilePayload.canPersist
          ? isClientAccount
            ? "Client account saved"
            : "Company saved"
          : "Saved settings — open the deep editor to set Industry & Stage.",
      );
      setMode("view");
      const meta = (updated.metadata ?? {}) as CompanyMetadata;
      form.reset({
        name: updated.name,
        short_name: meta.short_name ?? "",
        website: meta.website ?? "",
        about: updated.company_profile?.about ?? "",
        hiring_bar: updated.company_profile?.hiring_bar ?? "",
        default_timezone: meta.default_timezone,
        default_currency: meta.default_currency,
        default_locale: meta.default_locale,
        compliance_aivia_il: meta.compliance_aivia_il,
        compliance_gdpr_eu: meta.compliance_gdpr_eu,
        compliance_ccpa_ca: meta.compliance_ccpa_ca,
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

  const aboutCount = watched.about?.length ?? 0;
  const hiringBarCount = watched.hiring_bar?.length ?? 0;

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
            <div className="unit-about">
              <div className="unit-about-head">
                <span className="unit-about-label">About</span>
                {isEdit && (
                  <span className="unit-about-count">
                    {aboutCount} / {ABOUT_MAX} chars
                  </span>
                )}
              </div>
              <textarea
                className="textarea unit-about-body"
                rows={4}
                aria-label="About"
                placeholder="Describe what your company builds in 1-2 sentences."
                {...form.register("about")}
              />
            </div>
            <div className="locale-strip" aria-label="Locale & defaults">
              <LocaleChip
                label="Locale"
                variant={localeVariant}
                isEdit={isEdit}
                value={watched.default_locale}
                inheritedValue={
                  unit.inherited_locale?.values.default_locale ?? null
                }
                inheritedFromName={inheritedFromName}
                options={LOCALE_OPTIONS}
                commonValues={getLocaleCommonValues()}
                onChange={(v) => {
                  form.setValue("default_locale", v, { shouldDirty: true });
                  if (!v) return;
                  const d = localeDefaults(v);
                  if (d.timezone) {
                    form.setValue("default_timezone", d.timezone, {
                      shouldDirty: true,
                    });
                  }
                  if (d.currency) {
                    form.setValue("default_currency", d.currency, {
                      shouldDirty: true,
                    });
                  }
                }}
              />
              <LocaleChip
                label="Timezone"
                variant={localeVariant}
                isEdit={isEdit}
                value={watched.default_timezone}
                inheritedValue={
                  unit.inherited_locale?.values.default_timezone ?? null
                }
                inheritedFromName={inheritedFromName}
                options={TIMEZONE_OPTIONS}
                commonValues={getTimezoneCommonValues()}
                onChange={(v) =>
                  form.setValue("default_timezone", v, { shouldDirty: true })
                }
              />
              <LocaleChip
                label="Currency"
                variant={localeVariant}
                isEdit={isEdit}
                value={watched.default_currency}
                inheritedValue={
                  unit.inherited_locale?.values.default_currency ?? null
                }
                inheritedFromName={inheritedFromName}
                options={CURRENCY_OPTIONS}
                commonValues={CURRENCY_COMMON_VALUES}
                onChange={(v) =>
                  form.setValue("default_currency", v, { shouldDirty: true })
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
                <div className="profile-narrative-head">
                  <span className="profile-narrative-label">
                    Hiring bar narrative
                  </span>
                  {isEdit && (
                    <span className="profile-narrative-count">
                      {hiringBarCount} / {HIRING_BAR_MAX} chars
                    </span>
                  )}
                </div>
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
            {(!profile?.industry || !profile?.company_stage) && (
              <div
                className="empty-state"
                style={{ padding: "12px 4px", textAlign: "left" }}
              >
                Industry &amp; Stage are required to publish.{" "}
                <a
                  className="btn link"
                  href={`/settings/org-units/${unit.id}/company-profile`}
                >
                  Open the deep editor
                </a>{" "}
                to set them.
              </div>
            )}
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

          <div className="section-divider">
            {isClientAccount
              ? `Tenant defaults — inherited from ${inheritedFromName ?? "agency root"}`
              : "Tenant-wide defaults"}
          </div>

          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">Compliance flags</div>
                <div className="section-sub">
                  {isClientAccount
                    ? "Inherited from agency root. Override per flag for jobs anchored under this client."
                    : "Source of truth for the tenant. Regions and Client accounts may override per flag."}
                </div>
              </div>
            </div>
            <div className="card">
              {COMPLIANCE_FLAGS.map((flag) => (
                <ComplianceRow
                  key={flag.key}
                  flag={flag}
                  variant={complianceVariant}
                  isEdit={isEdit}
                  value={watched[flag.key]}
                  inheritedValue={
                    unit.inherited_compliance?.values[flag.key] ?? null
                  }
                  inheritedFromName={inheritedFromName}
                  onChange={(v) =>
                    form.setValue(flag.key, v, { shouldDirty: true })
                  }
                />
              ))}
            </div>
          </section>
        </div>

        <Sidebar
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          topCard={
            <SidebarMembersCard
              unitId={unit.id}
              isEdit={isEdit}
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
