"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { orgUnitsApi, type OrgUnit } from "@/lib/api/org-units";
import {
  UnitPageHeader,
  Section,
  Field,
  SmallStats,
} from "./shared";
import { MembersSection } from "./MembersSection";

interface DivisionMetadata {
  code?: string;
  lead_name?: string;
  cost_center?: string;
  hiring_budget?: string;
  description?: string;
  default_panel?: string;
  default_takehome?: string;
  default_tech_screen?: string;
  bar_raiser_pool?: string;
}

export function DivisionDetail({
  unit,
  parentPath,
  subUnits,
  onBack,
  onSaved,
  openRolesCount,
  openRolesByChildId,
}: {
  unit: OrgUnit;
  parentPath: string;
  subUnits: OrgUnit[];
  onBack: () => void;
  onSaved: (unit: OrgUnit) => void;
  openRolesCount: number;
  openRolesByChildId: Record<string, number>;
}) {
  const initial = (unit.metadata ?? {}) as DivisionMetadata;
  const [name, setName] = useState(unit.name);
  const [code, setCode] = useState(initial.code ?? "");
  const [leadName, setLeadName] = useState(initial.lead_name ?? "");
  const [costCenter, setCostCenter] = useState(initial.cost_center ?? "");
  const [hiringBudget, setHiringBudget] = useState(
    initial.hiring_budget ?? "",
  );
  const [description, setDescription] = useState(initial.description ?? "");
  const [defaultPanel, setDefaultPanel] = useState(
    initial.default_panel ?? "",
  );
  const [defaultTakehome, setDefaultTakehome] = useState(
    initial.default_takehome ?? "",
  );
  const [defaultTechScreen, setDefaultTechScreen] = useState(
    initial.default_tech_screen ?? "",
  );
  const [barRaiserPool, setBarRaiserPool] = useState(
    initial.bar_raiser_pool ?? "",
  );
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const token = await getFreshSupabaseToken();
      const updated = await orgUnitsApi.update(token, unit.id, {
        name: name.trim() || unit.name,
        metadata: {
          code,
          lead_name: leadName,
          cost_center: costCenter,
          hiring_budget: hiringBudget,
          description,
          default_panel: defaultPanel,
          default_takehome: defaultTakehome,
          default_tech_screen: defaultTechScreen,
          bar_raiser_pool: barRaiserPool,
        },
        set_metadata: true,
      });
      onSaved(updated);
      toast.success("Division saved");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to save division",
      );
    } finally {
      setSaving(false);
    }
  }

  const teams = subUnits.filter((u) => u.unit_type === "team");

  return (
    <>
      <UnitPageHeader
        type="division"
        name={unit.name}
        parentPath={parentPath}
        lead={leadName || null}
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
          <Section title="Division details">
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Division name">
                <input
                  className="px-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Code">
                <input
                  className="px-input mono"
                  value={code}
                  onChange={(e) => setCode(e.target.value.toUpperCase())}
                  placeholder="ENG"
                />
              </Field>
              <Field label="Division lead">
                <input
                  className="px-input"
                  value={leadName}
                  onChange={(e) => setLeadName(e.target.value)}
                  placeholder="Sam Rivera · VP Engineering"
                />
              </Field>
              <Field label="Rolls up to">
                <input
                  className="px-input"
                  value={parentPath}
                  readOnly
                />
              </Field>
              <Field label="Cost center">
                <input
                  className="px-input mono"
                  value={costCenter}
                  onChange={(e) => setCostCenter(e.target.value)}
                  placeholder="CC-401-ENG"
                />
              </Field>
              <Field label="Hiring budget">
                <input
                  className="px-input mono"
                  value={hiringBudget}
                  onChange={(e) => setHiringBudget(e.target.value)}
                  placeholder="$14.2M"
                />
              </Field>
            </div>
          </Section>

          <Section
            title="Description"
            sub="What this division does. Copilot appends this to every JD opened under it."
          >
            <textarea
              className="px-input"
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Engineering owns the platform, the product surface customers touch, and the infra that keeps both running…"
            />
          </Section>

          <Section
            title={`Teams under ${unit.name}`}
            right={
              <Button variant="outline" size="xs">
                + New team
              </Button>
            }
          >
            {teams.length === 0 ? (
              <div
                className="rounded-[10px] border px-4 py-6 text-center text-[12.5px]"
                style={{
                  borderColor: "var(--px-hairline)",
                  background: "var(--px-surface)",
                  color: "var(--px-fg-4)",
                }}
              >
                No teams yet.
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2.5">
                {teams.map((t) => {
                  const open = openRolesByChildId[t.id] ?? 0;
                  const pressure =
                    open >= 3 ? "hot" : open > 0 ? "steady" : "cool";
                  const dot =
                    pressure === "hot"
                      ? "var(--px-accent)"
                      : pressure === "steady"
                        ? "var(--px-ok)"
                        : "var(--px-fg-4)";
                  return (
                    <Link
                      key={t.id}
                      href={`/settings/org-units/${t.id}`}
                      className="block rounded-[10px] border p-3.5 transition-colors hover:brightness-[0.98]"
                      style={{
                        background: "var(--px-surface)",
                        borderColor: "var(--px-hairline)",
                      }}
                    >
                      <div className="mb-1.5 flex items-center gap-2">
                        <span
                          className="h-2 w-2 rounded-full"
                          style={{ background: dot }}
                        />
                        <div
                          className="text-[15px] font-semibold"
                          style={{ color: "var(--px-fg)" }}
                        >
                          {t.name}
                        </div>
                        <span className="flex-1" />
                        {open > 0 && (
                          <span
                            className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium"
                            style={{
                              background: "var(--px-accent-tint)",
                              color: "var(--px-accent)",
                              borderColor: "var(--px-accent-line)",
                            }}
                          >
                            {open} open
                          </span>
                        )}
                      </div>
                      <div
                        className="flex items-baseline gap-3.5 text-[11.5px]"
                        style={{ color: "var(--px-fg-4)" }}
                      >
                        <span>
                          <span
                            className="px-mono mr-1 text-[13px] font-medium"
                            style={{ color: "var(--px-fg-2)" }}
                          >
                            {t.member_count}
                          </span>
                          people
                        </span>
                        <span className="flex-1" />
                        <span style={{ color: "var(--px-accent)" }}>
                          Open →
                        </span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </Section>

          <Section
            title="Default interview panel"
            sub="Applied to any role created under this division unless the recruiter overrides."
          >
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Panel composition">
                <input
                  className="px-input"
                  value={defaultPanel}
                  onChange={(e) => setDefaultPanel(e.target.value)}
                  placeholder="1 HM · 2 peers · 1 bar raiser"
                />
              </Field>
              <Field label="Takehome">
                <input
                  className="px-input"
                  value={defaultTakehome}
                  onChange={(e) => setDefaultTakehome(e.target.value)}
                  placeholder="Off by default"
                />
              </Field>
              <Field label="Technical screen">
                <input
                  className="px-input"
                  value={defaultTechScreen}
                  onChange={(e) => setDefaultTechScreen(e.target.value)}
                  placeholder="System-design (45 min)"
                />
              </Field>
              <Field label="Bar raiser pool">
                <input
                  className="px-input"
                  value={barRaiserPool}
                  onChange={(e) => setBarRaiserPool(e.target.value)}
                  placeholder="Staff+ · cross-team · 18 people"
                />
              </Field>
            </div>
          </Section>

          <MembersSection unitId={unit.id} />
        </div>

        <aside className="flex flex-col gap-3.5">
          <SmallStats
            rows={[
              { l: "Teams", v: String(teams.length) },
              { l: "Headcount", v: String(unit.member_count) },
              {
                l: "Open roles",
                v: String(openRolesCount),
                ok: openRolesCount > 0,
              },
              { l: "Sub-units", v: String(subUnits.length) },
            ]}
          />
        </aside>
      </div>
    </>
  );
}
