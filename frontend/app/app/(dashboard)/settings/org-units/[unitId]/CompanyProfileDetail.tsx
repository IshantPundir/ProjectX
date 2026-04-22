"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { orgUnitsApi, type OrgUnit } from "@/lib/api/org-units";
import {
  INDUSTRY_OPTIONS,
  COMPANY_STAGE_OPTIONS,
  type CompanyProfile,
} from "@/components/dashboard/company-profile-form";
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

  const [name, setName] = useState(unit.name);
  // Core company_profile (persisted via set_company_profile)
  const [about, setAbout] = useState(profile?.about ?? "");
  const [industry, setIndustry] = useState<string>(profile?.industry ?? "");
  const [stage, setStage] = useState<string>(profile?.company_stage ?? "");
  const [hiringBar, setHiringBar] = useState(profile?.hiring_bar ?? "");

  // Extended metadata
  const [legalName, setLegalName] = useState(meta.legal_name ?? "");
  const [shortName, setShortName] = useState(meta.short_name ?? "");
  const [website, setWebsite] = useState(meta.website ?? "");
  const [hq, setHq] = useState(meta.hq ?? "");
  const [size, setSize] = useState(meta.size ?? "");
  const [interviewStyle, setInterviewStyle] = useState(
    meta.interview_style ?? "",
  );
  const [panelSize, setPanelSize] = useState(meta.panel_size ?? "");
  const [takehomePolicy, setTakehomePolicy] = useState(
    meta.takehome_policy ?? "",
  );
  const [timeToDecision, setTimeToDecision] = useState(
    meta.time_to_decision ?? "",
  );
  const [values, setValues] = useState(meta.values ?? "");
  const [basePhilosophy, setBasePhilosophy] = useState(
    meta.base_philosophy ?? "",
  );
  const [equity, setEquity] = useState(meta.equity ?? "");
  const [bonus, setBonus] = useState(meta.bonus ?? "");
  const [locations, setLocations] = useState<string[]>(meta.locations ?? []);
  const [locationInput, setLocationInput] = useState("");
  const [remotePolicy, setRemotePolicy] = useState(meta.remote_policy ?? "");
  const [visa, setVisa] = useState(meta.visa ?? "");

  // Client-only
  const [contractStart, setContractStart] = useState(
    meta.contract_start ?? "",
  );
  const [renews, setRenews] = useState(meta.renews ?? "");
  const [feeModel, setFeeModel] = useState(meta.fee_model ?? "");
  const [guaranteePeriod, setGuaranteePeriod] = useState(
    meta.guarantee_period ?? "",
  );
  const [exclusiveRoles, setExclusiveRoles] = useState(
    meta.exclusive_roles ?? "",
  );
  const [accountManager, setAccountManager] = useState(
    meta.account_manager ?? "",
  );

  const [saving, setSaving] = useState(false);

  function addLocation() {
    const v = locationInput.trim();
    if (!v) return;
    if (!locations.includes(v)) setLocations((prev) => [...prev, v]);
    setLocationInput("");
  }

  async function handleSave() {
    setSaving(true);
    try {
      const token = await getFreshSupabaseToken();

      // Persist the core 4-field company_profile only if all required
      // fields validate — the backend enforces non-empty `about`/`industry`/
      // `company_stage`/`hiring_bar` whenever set_company_profile is true.
      // If any are missing, skip it; metadata still saves.
      const canSaveProfile =
        about.trim().length >= 30 &&
        industry &&
        stage &&
        hiringBar.trim().length >= 20;

      const updated = await orgUnitsApi.update(token, unit.id, {
        name: name.trim() || unit.name,
        ...(canSaveProfile
          ? {
              company_profile: {
                about: about.trim(),
                industry,
                company_stage: stage,
                hiring_bar: hiringBar.trim(),
              } as CompanyProfile,
              set_company_profile: true,
            }
          : {}),
        metadata: {
          legal_name: legalName,
          short_name: shortName,
          website,
          hq,
          size,
          interview_style: interviewStyle,
          panel_size: panelSize,
          takehome_policy: takehomePolicy,
          time_to_decision: timeToDecision,
          values,
          base_philosophy: basePhilosophy,
          equity,
          bonus,
          locations,
          remote_policy: remotePolicy,
          visa,
          ...(isClient
            ? {
                contract_start: contractStart,
                renews,
                fee_model: feeModel,
                guarantee_period: guaranteePeriod,
                exclusive_roles: exclusiveRoles,
                account_manager: accountManager,
              }
            : {}),
        },
        set_metadata: true,
      });
      onSaved(updated);
      toast.success(isClient ? "Client saved" : "Company saved");
      if (!canSaveProfile && (about || industry || stage || hiringBar)) {
        toast.info(
          "Metadata saved. Fill all 4 core profile fields to save the company_profile.",
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
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
            <Button variant="ghost" size="sm">
              Archive
            </Button>
            <Button variant="outline" size="sm">
              Copy link
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save changes"}
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
                <input
                  className="px-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field
                label="Short name"
                hint="Appears in candidate-facing invites and interview chrome."
              >
                <input
                  className="px-input"
                  value={shortName}
                  onChange={(e) => setShortName(e.target.value)}
                />
              </Field>
              <Field label="Legal name">
                <input
                  className="px-input"
                  value={legalName}
                  onChange={(e) => setLegalName(e.target.value)}
                  placeholder={isClient ? "Northwind Labs, Inc." : "Acme Technologies, Inc."}
                />
              </Field>
              <Field label="Website">
                <input
                  className="px-input mono"
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  placeholder="acme.com"
                />
              </Field>
              <Field label="Industry">
                <select
                  className="px-input"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
                >
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
                  value={hq}
                  onChange={(e) => setHq(e.target.value)}
                  placeholder="San Francisco, CA"
                />
              </Field>
              <Field label="Company stage">
                <select
                  className="px-input"
                  value={stage}
                  onChange={(e) => setStage(e.target.value)}
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
                  value={size}
                  onChange={(e) => setSize(e.target.value)}
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
              value={about}
              onChange={(e) => setAbout(e.target.value)}
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
                  value={interviewStyle}
                  onChange={(e) => setInterviewStyle(e.target.value)}
                  placeholder="Structured · calibrated panels"
                />
              </Field>
              <Field label="Default panel size">
                <input
                  className="px-input"
                  value={panelSize}
                  onChange={(e) => setPanelSize(e.target.value)}
                  placeholder="4 interviewers (3 peers + 1 HM)"
                />
              </Field>
              <Field
                label="Takehomes"
                hint="Recruiters can still opt in per role."
              >
                <input
                  className="px-input"
                  value={takehomePolicy}
                  onChange={(e) => setTakehomePolicy(e.target.value)}
                  placeholder="Rare — only for senior IC roles"
                />
              </Field>
              <Field label="Typical time-to-decision">
                <input
                  className="px-input"
                  value={timeToDecision}
                  onChange={(e) => setTimeToDecision(e.target.value)}
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
                  value={values}
                  onChange={(e) => setValues(e.target.value)}
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
                  value={hiringBar}
                  onChange={(e) => setHiringBar(e.target.value)}
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
                  value={basePhilosophy}
                  onChange={(e) => setBasePhilosophy(e.target.value)}
                  placeholder="75th percentile, market-adjusted"
                />
              </Field>
              <Field label="Equity grant">
                <input
                  className="px-input"
                  value={equity}
                  onChange={(e) => setEquity(e.target.value)}
                  placeholder="Standard — 4yr / 1yr cliff"
                />
              </Field>
              <Field label="Bonus">
                <input
                  className="px-input"
                  value={bonus}
                  onChange={(e) => setBonus(e.target.value)}
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
                  onRemove={() =>
                    setLocations((prev) => prev.filter((l) => l !== loc))
                  }
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
                <Button variant="ghost" size="xs" onClick={addLocation}>
                  + Add
                </Button>
              </div>
            </div>
            <div className="mt-3.5 grid grid-cols-2 gap-3.5">
              <Field label="Remote policy">
                <input
                  className="px-input"
                  value={remotePolicy}
                  onChange={(e) => setRemotePolicy(e.target.value)}
                  placeholder="Remote-friendly · HQ optional"
                />
              </Field>
              <Field label="Visa sponsorship">
                <input
                  className="px-input"
                  value={visa}
                  onChange={(e) => setVisa(e.target.value)}
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
                    value={contractStart}
                    onChange={(e) => setContractStart(e.target.value)}
                    placeholder="2025-02-03"
                  />
                </Field>
                <Field label="Renews">
                  <input
                    className="px-input mono"
                    value={renews}
                    onChange={(e) => setRenews(e.target.value)}
                    placeholder="2026-02-03"
                  />
                </Field>
                <Field label="Fee model">
                  <input
                    className="px-input"
                    value={feeModel}
                    onChange={(e) => setFeeModel(e.target.value)}
                    placeholder="22% placement fee"
                  />
                </Field>
                <Field label="Guarantee period">
                  <input
                    className="px-input"
                    value={guaranteePeriod}
                    onChange={(e) => setGuaranteePeriod(e.target.value)}
                    placeholder="90 days"
                  />
                </Field>
                <Field label="Exclusive roles">
                  <input
                    className="px-input"
                    value={exclusiveRoles}
                    onChange={(e) => setExclusiveRoles(e.target.value)}
                    placeholder="Senior IC only"
                  />
                </Field>
                <Field label="Account manager">
                  <input
                    className="px-input"
                    value={accountManager}
                    onChange={(e) => setAccountManager(e.target.value)}
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
    </>
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
