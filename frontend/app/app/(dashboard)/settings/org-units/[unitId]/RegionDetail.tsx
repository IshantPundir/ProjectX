"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import { type OrgUnit, type RegionMetadata } from "@/lib/api/org-units";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";

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
  mergeMetadata,
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
  const metadata = (unit.metadata ?? {}) as RegionMetadata;
  const [mode, setMode] = React.useState<"view" | "edit">("view");

  const defaults = React.useMemo<RegionFormValues>(
    () => ({
      name: unit.name,
      default_timezone: metadata.default_timezone,
      default_currency: metadata.default_currency,
      default_locale: metadata.default_locale,
      compliance_aivia_il: metadata.compliance_aivia_il,
      compliance_gdpr_eu: metadata.compliance_gdpr_eu,
      compliance_ccpa_ca: metadata.compliance_ccpa_ca,
    }),
    [
      unit.name,
      metadata.default_timezone,
      metadata.default_currency,
      metadata.default_locale,
      metadata.compliance_aivia_il,
      metadata.compliance_gdpr_eu,
      metadata.compliance_ccpa_ca,
    ],
  );

  const form = useForm<RegionFormValues>({
    resolver: zodResolver(regionFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    form.reset(defaults);
  }, [defaults, form]);

  const updateMutation = useUpdateOrgUnit();
  const watched = form.watch();
  const isEdit = mode === "edit";

  const inheritedFromName = React.useMemo(() => {
    const sourceId =
      unit.inherited_locale?.source_unit_id ??
      unit.inherited_compliance?.source_unit_id ??
      null;
    if (!sourceId || sourceId === unit.id) return null;
    return parentChain.find((u) => u.id === sourceId)?.name ?? null;
  }, [
    unit.id,
    unit.inherited_locale?.source_unit_id,
    unit.inherited_compliance?.source_unit_id,
    parentChain,
  ]);

  async function onSubmit(values: RegionFormValues) {
    try {
      const merged = mergeMetadata(unit.metadata, {
        default_timezone: values.default_timezone,
        default_currency: values.default_currency,
        default_locale: values.default_locale,
        compliance_aivia_il: values.compliance_aivia_il,
        compliance_gdpr_eu: values.compliance_gdpr_eu,
        compliance_ccpa_ca: values.compliance_ccpa_ca,
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
      toast.success("Region saved");
      setMode("view");
      const meta = (updated.metadata ?? {}) as RegionMetadata;
      form.reset({
        name: updated.name,
        default_timezone: meta.default_timezone,
        default_currency: meta.default_currency,
        default_locale: meta.default_locale,
        compliance_aivia_il: meta.compliance_aivia_il,
        compliance_gdpr_eu: meta.compliance_gdpr_eu,
        compliance_ccpa_ca: meta.compliance_ccpa_ca,
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
            <div className="locale-strip" aria-label="Locale & defaults">
              <LocaleChip
                label="Locale"
                variant="inherit"
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
                variant="inherit"
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
                variant="inherit"
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

          <div className="section-divider">
            Tenant defaults — inherited from {inheritedFromName ?? "tenant"}
          </div>

          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">Compliance flags</div>
                <div className="section-sub">
                  Inherited from parent. Toggle to override per-region.
                </div>
              </div>
            </div>
            <div className="card">
              {COMPLIANCE_FLAGS.map((flag) => (
                <ComplianceRow
                  key={flag.key}
                  flag={flag}
                  variant="inherit"
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
              helperText="Often empty — most members live at division/team level. Useful for regional HR partners and legal."
            />
          }
        />
      </div>
    </main>
  );
}
