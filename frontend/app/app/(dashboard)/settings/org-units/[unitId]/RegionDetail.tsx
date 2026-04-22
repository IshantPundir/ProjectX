"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { orgUnitsApi, type OrgUnit } from "@/lib/api/org-units";
import {
  UnitPageHeader,
  Section,
  Field,
  SubUnitsList,
  SmallStats,
} from "./shared";
import { MembersSection } from "./MembersSection";

interface Office {
  city: string;
  addr: string;
  seats: number;
  status: string;
}

interface RegionMetadata {
  code?: string;
  primary_city?: string;
  timezone?: string;
  currency?: string;
  locale?: string;
  offices?: Office[];
  notes?: string;
  lead_name?: string;
}

export function RegionDetail({
  unit,
  parentPath,
  subUnits,
  onBack,
  onSaved,
  openRolesCount,
}: {
  unit: OrgUnit;
  parentPath: string;
  subUnits: OrgUnit[];
  onBack: () => void;
  onSaved: (unit: OrgUnit) => void;
  openRolesCount: number;
}) {
  const initial = (unit.metadata ?? {}) as RegionMetadata;
  const [name, setName] = useState(unit.name);
  const [code, setCode] = useState(initial.code ?? "");
  const [primaryCity, setPrimaryCity] = useState(initial.primary_city ?? "");
  const [timezone, setTimezone] = useState(initial.timezone ?? "");
  const [currency, setCurrency] = useState(initial.currency ?? "");
  const [locale, setLocale] = useState(initial.locale ?? "");
  const [notes, setNotes] = useState(initial.notes ?? "");
  const [leadName, setLeadName] = useState(initial.lead_name ?? "");
  const [offices, setOffices] = useState<Office[]>(initial.offices ?? []);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const token = await getFreshSupabaseToken();
      const updated = await orgUnitsApi.update(token, unit.id, {
        name: name.trim() || unit.name,
        metadata: {
          code,
          primary_city: primaryCity,
          timezone,
          currency,
          locale,
          offices,
          notes,
          lead_name: leadName,
        },
        set_metadata: true,
      });
      onSaved(updated);
      toast.success("Region saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save region");
    } finally {
      setSaving(false);
    }
  }

  function updateOffice(i: number, patch: Partial<Office>) {
    setOffices((prev) => prev.map((o, idx) => (idx === i ? { ...o, ...patch } : o)));
  }
  function addOffice() {
    setOffices((prev) => [
      ...prev,
      { city: "", addr: "", seats: 0, status: "Active" },
    ]);
  }
  function removeOffice(i: number) {
    setOffices((prev) => prev.filter((_, idx) => idx !== i));
  }

  return (
    <>
      <UnitPageHeader
        type="region"
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
          <Section
            title="Region details"
            sub="A region is a geography, not a reporting line. Division and team headcount roll up to it."
          >
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Region name">
                <input
                  className="px-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Code" hint="Used in role IDs and payroll exports.">
                <input
                  className="px-input mono"
                  value={code}
                  onChange={(e) => setCode(e.target.value.toUpperCase())}
                  placeholder="AMER"
                />
              </Field>
              <Field label="Primary city">
                <input
                  className="px-input"
                  value={primaryCity}
                  onChange={(e) => setPrimaryCity(e.target.value)}
                  placeholder="San Francisco, CA"
                />
              </Field>
              <Field label="Timezone base">
                <input
                  className="px-input"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="America/Los_Angeles"
                />
              </Field>
              <Field label="Currency">
                <input
                  className="px-input"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  placeholder="USD"
                />
              </Field>
              <Field label="Locale">
                <input
                  className="px-input mono"
                  value={locale}
                  onChange={(e) => setLocale(e.target.value)}
                  placeholder="en-US"
                />
              </Field>
              <Field label="Region lead" span={2}>
                <input
                  className="px-input"
                  value={leadName}
                  onChange={(e) => setLeadName(e.target.value)}
                  placeholder="Sam Rivera · VP"
                />
              </Field>
            </div>
          </Section>

          <Section
            title="Offices in this region"
            right={
              <Button variant="outline" size="xs" onClick={addOffice}>
                + Add office
              </Button>
            }
          >
            <div
              className="overflow-hidden rounded-[10px] border"
              style={{
                background: "var(--px-surface)",
                borderColor: "var(--px-hairline)",
              }}
            >
              {offices.length === 0 ? (
                <div
                  className="px-4 py-8 text-center text-[12.5px]"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  No offices yet. Click <b>Add office</b> to add one.
                </div>
              ) : (
                offices.map((o, i) => (
                  <div
                    key={i}
                    className="grid items-center gap-3.5 px-3.5 py-3"
                    style={{
                      gridTemplateColumns: "1.2fr 2fr 100px 120px 30px",
                      borderBottom:
                        i < offices.length - 1
                          ? "1px solid var(--px-divider)"
                          : "none",
                    }}
                  >
                    <input
                      className="px-input sm"
                      value={o.city}
                      onChange={(e) =>
                        updateOffice(i, { city: e.target.value })
                      }
                      placeholder="City"
                    />
                    <input
                      className="px-input sm mono"
                      value={o.addr}
                      onChange={(e) =>
                        updateOffice(i, { addr: e.target.value })
                      }
                      placeholder="Address"
                    />
                    <input
                      className="px-input sm mono"
                      type="number"
                      min={0}
                      value={o.seats || ""}
                      onChange={(e) =>
                        updateOffice(i, {
                          seats: Math.max(0, Number(e.target.value) || 0),
                        })
                      }
                      placeholder="seats"
                    />
                    <select
                      className="px-input sm"
                      value={o.status}
                      onChange={(e) =>
                        updateOffice(i, { status: e.target.value })
                      }
                    >
                      <option value="Primary">Primary</option>
                      <option value="Active">Active</option>
                      <option value="Closed">Closed</option>
                    </select>
                    <button
                      type="button"
                      onClick={() => removeOffice(i)}
                      className="cursor-pointer"
                      style={{ color: "var(--px-fg-4)" }}
                      aria-label="Remove office"
                    >
                      ×
                    </button>
                  </div>
                ))
              )}
            </div>
          </Section>

          <Section
            title="Regional hiring notes"
            sub="Optional — overrides the company profile when Copilot writes roles in this region."
          >
            <textarea
              className="px-input"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. We sponsor TN and H-1B from the US offices. Pay bands posted on all job descriptions in NY, CA, CO, WA."
            />
          </Section>

          <MembersSection unitId={unit.id} />
        </div>

        <aside className="flex flex-col gap-3.5">
          <SmallStats
            rows={[
              { l: "Offices", v: String(offices.length) },
              { l: "Headcount", v: String(unit.member_count) },
              {
                l: "Open roles",
                v: String(openRolesCount),
                ok: openRolesCount > 0,
              },
              { l: "Sub-units", v: String(subUnits.length) },
            ]}
          />
          <SubUnitsList subUnits={subUnits} />
        </aside>
      </div>
    </>
  );
}
