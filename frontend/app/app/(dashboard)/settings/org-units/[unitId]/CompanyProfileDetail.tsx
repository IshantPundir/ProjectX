"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { type OrgUnit } from "@/lib/api/org-units";
import {
  INDUSTRY_OPTIONS,
  COMPANY_STAGE_OPTIONS,
  type CompanyProfile,
} from "@/components/dashboard/company-profile-form";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { applyApiErrorToForm } from "@/lib/api/errors";
import {
  UnitPageHeader,
  Section,
  Field,
  SubUnitsList,
  TagChip,
} from "./shared";
import { MembersSection } from "./MembersSection";

interface CompanyMetadata {
  legal_name?: string;
  short_name?: string;
  website?: string;
  hq?: string;
  size?: string;
  interview_style?: string;
  panel_size?: string;
  takehome_policy?: string;
  time_to_decision?: string;
  values?: string;
  base_philosophy?: string;
  equity?: string;
  bonus?: string;
  locations?: string[];
  remote_policy?: string;
  visa?: string;
  // Client-only
  contract_start?: string;
  renews?: string;
  fee_model?: string;
  guarantee_period?: string;
  exclusive_roles?: string;
  account_manager?: string;
}

const companyDetailSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),

  // Core company_profile — validated against the same 30/20 char rules
  // as CompanyProfileForm. Empty is allowed; save logic gates on all-or-nothing.
  about: z.string(),
  industry: z.string(),
  company_stage: z.string(),
  hiring_bar: z.string(),

  // Extended metadata — all optional text fields
  legal_name: z.string(),
  short_name: z.string(),
  website: z.string(),
  hq: z.string(),
  size: z.string(),
  interview_style: z.string(),
  panel_size: z.string(),
  takehome_policy: z.string(),
  time_to_decision: z.string(),
  values: z.string(),
  base_philosophy: z.string(),
  equity: z.string(),
  bonus: z.string(),
  locations: z.array(z.string()),
  remote_policy: z.string(),
  visa: z.string(),

  // Client-only
  contract_start: z.string(),
  renews: z.string(),
  fee_model: z.string(),
  guarantee_period: z.string(),
  exclusive_roles: z.string(),
  account_manager: z.string(),
});

type CompanyDetailFormValues = z.infer<typeof companyDetailSchema>;

export function CompanyProfileDetail({
  unit,
  subUnits,
  onBack,
  onSaved,
  openRolesCount,
}: {
  unit: OrgUnit;
  subUnits: OrgUnit[];
  onBack: () => void;
  onSaved: (unit: OrgUnit) => void;
  openRolesCount: number;
}) {
  const isClient = unit.unit_type === "client_account";

  const profile = unit.company_profile;
  const meta = (unit.metadata ?? {}) as CompanyMetadata;

  const form = useForm<CompanyDetailFormValues>({
    resolver: zodResolver(companyDetailSchema),
    defaultValues: {
      name: unit.name,
      about: profile?.about ?? "",
      industry: profile?.industry ?? "",
      company_stage: profile?.company_stage ?? "",
      hiring_bar: profile?.hiring_bar ?? "",
      legal_name: meta.legal_name ?? "",
      short_name: meta.short_name ?? "",
      website: meta.website ?? "",
      hq: meta.hq ?? "",
      size: meta.size ?? "",
      interview_style: meta.interview_style ?? "",
      panel_size: meta.panel_size ?? "",
      takehome_policy: meta.takehome_policy ?? "",
      time_to_decision: meta.time_to_decision ?? "",
      values: meta.values ?? "",
      base_philosophy: meta.base_philosophy ?? "",
      equity: meta.equity ?? "",
      bonus: meta.bonus ?? "",
      locations: meta.locations ?? [],
      remote_policy: meta.remote_policy ?? "",
      visa: meta.visa ?? "",
      contract_start: meta.contract_start ?? "",
      renews: meta.renews ?? "",
      fee_model: meta.fee_model ?? "",
      guarantee_period: meta.guarantee_period ?? "",
      exclusive_roles: meta.exclusive_roles ?? "",
      account_manager: meta.account_manager ?? "",
    },
  });

  const updateMutation = useUpdateOrgUnit();

  // locationInput is pure UI state for the tag-chip input (not a form field)
  const [locationInput, setLocationInput] = useState("");

  // Watch reactive values used in the render (locations list + signals card)
  const locations = form.watch("locations");
  const about = form.watch("about");
  const hiringBar = form.watch("hiring_bar");
  const industry = form.watch("industry");
  const stage = form.watch("company_stage");
  const remotePolicy = form.watch("remote_policy");
  const interviewStyle = form.watch("interview_style");
  const accountManager = form.watch("account_manager");

  function addLocation() {
    const v = locationInput.trim();
    if (!v) return;
    const current = form.getValues("locations");
    if (!current.includes(v)) {
      form.setValue("locations", [...current, v], { shouldDirty: true });
    }
    setLocationInput("");
  }

  function removeLocation(loc: string) {
    const current = form.getValues("locations");
    form.setValue(
      "locations",
      current.filter((l) => l !== loc),
      { shouldDirty: true },
    );
  }

  async function onSubmit(values: CompanyDetailFormValues) {
    // Persist the core 4-field company_profile only if all required
    // fields validate — the backend enforces non-empty `about`/`industry`/
    // `company_stage`/`hiring_bar` whenever set_company_profile is true.
    // If any are missing, skip it; metadata still saves.
    const canSaveProfile =
      values.about.trim().length >= 30 &&
      values.industry &&
      values.company_stage &&
      values.hiring_bar.trim().length >= 20;

    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          ...(canSaveProfile
            ? {
                company_profile: {
                  about: values.about.trim(),
                  industry: values.industry,
                  company_stage: values.company_stage,
                  hiring_bar: values.hiring_bar.trim(),
                } as CompanyProfile,
                set_company_profile: true,
              }
            : {}),
          metadata: {
            legal_name: values.legal_name,
            short_name: values.short_name,
            website: values.website,
            hq: values.hq,
            size: values.size,
            interview_style: values.interview_style,
            panel_size: values.panel_size,
            takehome_policy: values.takehome_policy,
            time_to_decision: values.time_to_decision,
            values: values.values,
            base_philosophy: values.base_philosophy,
            equity: values.equity,
            bonus: values.bonus,
            locations: values.locations,
            remote_policy: values.remote_policy,
            visa: values.visa,
            ...(isClient
              ? {
                  contract_start: values.contract_start,
                  renews: values.renews,
                  fee_model: values.fee_model,
                  guarantee_period: values.guarantee_period,
                  exclusive_roles: values.exclusive_roles,
                  account_manager: values.account_manager,
                }
              : {}),
          },
          set_metadata: true,
        },
      });
      onSaved(updated);
      toast.success(isClient ? "Client saved" : "Company saved");
      if (
        !canSaveProfile &&
        (values.about || values.industry || values.company_stage || values.hiring_bar)
      ) {
        toast.info(
          "Metadata saved. Fill all 4 core profile fields to save the company_profile.",
        );
      }
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)}>
      <UnitPageHeader
        type={unit.unit_type}
        name={unit.name}
        parentPath={isClient ? "Clients" : null}
        lead={accountManager || null}
        people={unit.member_count}
        openRoles={openRolesCount}
        onBack={onBack}
        right={
          <>
            <Button variant="ghost" size="sm" type="button">
              Archive
            </Button>
            <Button variant="outline" size="sm" type="button">
              Copy link
            </Button>
            <Button size="sm" type="submit" disabled={updateMutation.isPending}>
              {updateMutation.isPending ? "Saving…" : "Save changes"}
            </Button>
          </>
        }
      />

      <div
        className="grid items-start gap-7 px-8 pb-10 pt-5"
        style={{ gridTemplateColumns: "1fr 320px" }}
      >
        <div>
          <Section title="Identity">
            <div className="grid grid-cols-2 gap-3.5">
              <Field label={isClient ? "Client name" : "Company name"}>
                <input className="px-input" {...form.register("name")} />
              </Field>
              <Field
                label="Short name"
                hint="Appears in candidate-facing invites and interview chrome."
              >
                <input className="px-input" {...form.register("short_name")} />
              </Field>
              <Field label="Legal name">
                <input
                  className="px-input"
                  {...form.register("legal_name")}
                  placeholder={isClient ? "Northwind Labs, Inc." : "Acme Technologies, Inc."}
                />
              </Field>
              <Field label="Website">
                <input
                  className="px-input mono"
                  {...form.register("website")}
                  placeholder="acme.com"
                />
              </Field>
              <Field label="Industry">
                <select className="px-input" {...form.register("industry")}>
                  <option value="" disabled>
                    Select industry
                  </option>
                  {INDUSTRY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Headquarters">
                <input
                  className="px-input"
                  {...form.register("hq")}
                  placeholder="San Francisco, CA"
                />
              </Field>
              <Field label="Company stage">
                <select
                  className="px-input"
                  {...form.register("company_stage")}
                >
                  <option value="" disabled>
                    Select stage
                  </option>
                  {COMPANY_STAGE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Size">
                <input
                  className="px-input"
                  {...form.register("size")}
                  placeholder="501–1,000 employees"
                />
              </Field>
            </div>
          </Section>

          <Section
            title="What the company does"
            sub="Written in plain language. Copilot uses this as the single source of truth when it enriches JDs or answers candidate questions."
          >
            <textarea
              className="px-input"
              rows={5}
              {...form.register("about")}
              placeholder="Describe the problems you solve, at what scale, for whom. Not your mission statement."
            />
            <div className="mt-1 flex items-center justify-between">
              <div className="px-hint">{about.length} / 500 characters</div>
              {about.length > 0 && about.length < 30 && (
                <div
                  className="text-[11px]"
                  style={{ color: "var(--px-caution)" }}
                >
                  30+ characters to save profile
                </div>
              )}
            </div>
          </Section>

          <Section
            title="Hiring culture"
            sub="What candidates should expect — and what interviewers are graded against."
          >
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Interview style">
                <input
                  className="px-input"
                  {...form.register("interview_style")}
                  placeholder="Structured · calibrated panels"
                />
              </Field>
              <Field label="Default panel size">
                <input
                  className="px-input"
                  {...form.register("panel_size")}
                  placeholder="4 interviewers (3 peers + 1 HM)"
                />
              </Field>
              <Field
                label="Takehomes"
                hint="Recruiters can still opt in per role."
              >
                <input
                  className="px-input"
                  {...form.register("takehome_policy")}
                  placeholder="Rare — only for senior IC roles"
                />
              </Field>
              <Field label="Typical time-to-decision">
                <input
                  className="px-input"
                  {...form.register("time_to_decision")}
                  placeholder="10 business days"
                />
              </Field>
            </div>
            <div className="mt-3.5">
              <Field
                label="What we value in hires"
                hint="Copilot cites these when it writes JD intros and rejection rationales."
              >
                <textarea
                  className="px-input"
                  rows={3}
                  {...form.register("values")}
                />
              </Field>
            </div>
            <div className="mt-3.5">
              <Field
                label="What a strong hire looks like"
                hint="Twitter-length — 280 characters max. Required for the company profile."
              >
                <textarea
                  className="px-input"
                  rows={3}
                  {...form.register("hiring_bar")}
                  maxLength={280}
                />
              </Field>
              <div className="mt-1 flex items-center justify-between">
                <div className="px-hint">{hiringBar.length} / 280 characters</div>
                {hiringBar.length > 0 && hiringBar.length < 20 && (
                  <div
                    className="text-[11px]"
                    style={{ color: "var(--px-caution)" }}
                  >
                    20+ characters to save profile
                  </div>
                )}
              </div>
            </div>
          </Section>

          <Section
            title="Compensation & equity"
            sub="Ranges Copilot can cite on the candidate side. Not shown to candidates unless you explicitly publish a role."
          >
            <div className="grid grid-cols-3 gap-3.5">
              <Field label="Base salary philosophy">
                <input
                  className="px-input"
                  {...form.register("base_philosophy")}
                  placeholder="75th percentile, market-adjusted"
                />
              </Field>
              <Field label="Equity grant">
                <input
                  className="px-input"
                  {...form.register("equity")}
                  placeholder="Standard — 4yr / 1yr cliff"
                />
              </Field>
              <Field label="Bonus">
                <input
                  className="px-input"
                  {...form.register("bonus")}
                  placeholder="None (equity-weighted)"
                />
              </Field>
            </div>
          </Section>

          <Section title="Locations & working model">
            <div className="flex flex-wrap items-center gap-2">
              {locations.map((loc) => (
                <TagChip
                  key={loc}
                  text={loc}
                  tone="soft"
                  onRemove={() => removeLocation(loc)}
                />
              ))}
              <div className="flex items-center gap-2">
                <input
                  className="px-input sm"
                  style={{ width: 200 }}
                  value={locationInput}
                  onChange={(e) => setLocationInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLocation();
                    }
                  }}
                  placeholder="e.g. San Francisco, CA"
                />
                <Button
                  variant="ghost"
                  size="xs"
                  type="button"
                  onClick={addLocation}
                >
                  + Add
                </Button>
              </div>
            </div>
            <div className="mt-3.5 grid grid-cols-2 gap-3.5">
              <Field label="Remote policy">
                <input
                  className="px-input"
                  {...form.register("remote_policy")}
                  placeholder="Remote-friendly · HQ optional"
                />
              </Field>
              <Field label="Visa sponsorship">
                <input
                  className="px-input"
                  {...form.register("visa")}
                  placeholder="Yes — H-1B, O-1, EU Blue Card"
                />
              </Field>
            </div>
          </Section>

          {isClient && (
            <Section
              title="Contract & SLA"
              sub="Agency-side fields. Not visible to the client's team."
            >
              <div className="grid grid-cols-3 gap-3.5">
                <Field label="Contract start">
                  <input
                    className="px-input mono"
                    {...form.register("contract_start")}
                    placeholder="2025-02-03"
                  />
                </Field>
                <Field label="Renews">
                  <input
                    className="px-input mono"
                    {...form.register("renews")}
                    placeholder="2026-02-03"
                  />
                </Field>
                <Field label="Fee model">
                  <input
                    className="px-input"
                    {...form.register("fee_model")}
                    placeholder="22% placement fee"
                  />
                </Field>
                <Field label="Guarantee period">
                  <input
                    className="px-input"
                    {...form.register("guarantee_period")}
                    placeholder="90 days"
                  />
                </Field>
                <Field label="Exclusive roles">
                  <input
                    className="px-input"
                    {...form.register("exclusive_roles")}
                    placeholder="Senior IC only"
                  />
                </Field>
                <Field label="Account manager">
                  <input
                    className="px-input"
                    {...form.register("account_manager")}
                  />
                </Field>
              </div>
            </Section>
          )}

          <MembersSection unitId={unit.id} />
        </div>

        <aside className="flex flex-col gap-3.5">
          <CopilotSignalsCard
            hasProfile={Boolean(unit.company_profile)}
            industry={industry}
            stage={stage}
            remotePolicy={remotePolicy}
            interviewStyle={interviewStyle}
          />
          <SubUnitsList subUnits={subUnits} />
        </aside>
      </div>
    </form>
  );
}

function CopilotSignalsCard({
  hasProfile,
  industry,
  stage,
  remotePolicy,
  interviewStyle,
}: {
  hasProfile: boolean;
  industry: string;
  stage: string;
  remotePolicy: string;
  interviewStyle: string;
}) {
  const signals: { l: string; v: string; src: "extracted" | "inferred" }[] =
    [];
  if (industry) signals.push({ l: "Industry", v: industry, src: "extracted" });
  if (stage) signals.push({ l: "Stage", v: stage, src: "extracted" });
  if (remotePolicy)
    signals.push({ l: "Remote policy", v: remotePolicy, src: "extracted" });
  if (interviewStyle)
    signals.push({
      l: "Interview style",
      v: interviewStyle,
      src: "extracted",
    });

  return (
    <div
      className="rounded-[10px] border p-4"
      style={{
        background: "var(--px-accent-tint)",
        borderColor: "var(--px-accent-line)",
      }}
    >
      <div className="mb-2.5 flex items-center gap-2">
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ color: "var(--px-accent)" }}
          aria-hidden="true"
        >
          <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
        </svg>
        <span
          className="text-[12px] font-semibold"
          style={{ color: "var(--px-accent)" }}
        >
          Copilot signals
        </span>
        <span className="flex-1" />
        <span
          className="px-mono text-[10.5px]"
          style={{ color: "var(--px-accent)" }}
        >
          {signals.length}
        </span>
      </div>
      <div
        className="mb-3 text-[12px]"
        style={{ color: "var(--px-fg-2)", lineHeight: 1.5 }}
      >
        {hasProfile
          ? "Pulled from this profile. Read into every JD Copilot enriches."
          : "Save the profile to surface signals Copilot can cite in JDs."}
      </div>
      <div className="flex flex-col gap-1.5">
        {signals.length === 0 ? (
          <div
            className="rounded-md border px-2.5 py-2 text-[11.5px] italic"
            style={{
              background: "var(--px-surface)",
              borderColor: "var(--px-hairline)",
              color: "var(--px-fg-4)",
            }}
          >
            No signals yet.
          </div>
        ) : (
          signals.map((s) => (
            <div
              key={s.l}
              className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12px]"
              style={{
                background: "var(--px-surface)",
                borderColor: "var(--px-hairline)",
              }}
            >
              <span className="flex-1" style={{ color: "var(--px-fg-3)" }}>
                {s.l}
              </span>
              <span
                className="truncate font-medium"
                style={{ color: "var(--px-fg)", maxWidth: 140 }}
                title={s.v}
              >
                {s.v}
              </span>
              <span
                className="rounded-full border px-1.5 py-0.5 text-[9.5px] font-semibold uppercase"
                style={{
                  letterSpacing: "0.3px",
                  background:
                    s.src === "extracted"
                      ? "var(--px-ai-bg)"
                      : "var(--px-caution-bg)",
                  color:
                    s.src === "extracted"
                      ? "var(--px-ai)"
                      : "var(--px-caution)",
                  borderColor:
                    s.src === "extracted"
                      ? "var(--px-ai-line)"
                      : "var(--px-caution-line)",
                  borderStyle: "dashed",
                }}
              >
                {s.src}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
