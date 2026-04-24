"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { type OrgUnit } from "@/lib/api/org-units";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { applyApiErrorToForm } from "@/lib/api/errors";
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

const officeSchema = z.object({
  city: z.string(),
  addr: z.string(),
  seats: z.number(),
  status: z.string(),
});

const regionDetailSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),
  code: z.string(),
  primary_city: z.string(),
  timezone: z.string(),
  currency: z.string(),
  locale: z.string(),
  notes: z.string(),
  lead_name: z.string(),
  offices: z.array(officeSchema),
});

type RegionDetailFormValues = z.infer<typeof regionDetailSchema>;

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

  const form = useForm<RegionDetailFormValues>({
    resolver: zodResolver(regionDetailSchema),
    defaultValues: {
      name: unit.name,
      code: initial.code ?? "",
      primary_city: initial.primary_city ?? "",
      timezone: initial.timezone ?? "",
      currency: initial.currency ?? "",
      locale: initial.locale ?? "",
      notes: initial.notes ?? "",
      lead_name: initial.lead_name ?? "",
      offices: initial.offices ?? [],
    },
  });

  const updateMutation = useUpdateOrgUnit();

  // Reactive values used in the render chrome.
  const leadName = form.watch("lead_name");
  const offices = form.watch("offices");

  async function onSubmit(values: RegionDetailFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          metadata: {
            code: values.code,
            primary_city: values.primary_city,
            timezone: values.timezone,
            currency: values.currency,
            locale: values.locale,
            offices: values.offices,
            notes: values.notes,
            lead_name: values.lead_name,
          },
          set_metadata: true,
        },
      });
      onSaved(updated);
      toast.success("Region saved");
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save region");
    }
  }

  function updateOffice(i: number, patch: Partial<Office>) {
    const current = form.getValues("offices");
    form.setValue(
      "offices",
      current.map((o, idx) => (idx === i ? { ...o, ...patch } : o)),
      { shouldDirty: true },
    );
  }
  function addOffice() {
    const current = form.getValues("offices");
    form.setValue(
      "offices",
      [...current, { city: "", addr: "", seats: 0, status: "Active" }],
      { shouldDirty: true },
    );
  }
  function removeOffice(i: number) {
    const current = form.getValues("offices");
    form.setValue(
      "offices",
      current.filter((_, idx) => idx !== i),
      { shouldDirty: true },
    );
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)}>
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
          <Section
            title="Region details"
            sub="A region is a geography, not a reporting line. Division and team headcount roll up to it."
          >
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Region name">
                <input className="px-input" {...form.register("name")} />
              </Field>
              <Field label="Code" hint="Used in role IDs and payroll exports.">
                <input
                  className="px-input mono"
                  {...form.register("code", {
                    setValueAs: (v: unknown) =>
                      typeof v === "string" ? v.toUpperCase() : "",
                  })}
                  placeholder="AMER"
                />
              </Field>
              <Field label="Primary city">
                <input
                  className="px-input"
                  {...form.register("primary_city")}
                  placeholder="San Francisco, CA"
                />
              </Field>
              <Field label="Timezone base">
                <input
                  className="px-input"
                  {...form.register("timezone")}
                  placeholder="America/Los_Angeles"
                />
              </Field>
              <Field label="Currency">
                <input
                  className="px-input"
                  {...form.register("currency")}
                  placeholder="USD"
                />
              </Field>
              <Field label="Locale">
                <input
                  className="px-input mono"
                  {...form.register("locale")}
                  placeholder="en-US"
                />
              </Field>
              <Field label="Region lead" span={2}>
                <input
                  className="px-input"
                  {...form.register("lead_name")}
                  placeholder="Sam Rivera · VP"
                />
              </Field>
            </div>
          </Section>

          <Section
            title="Offices in this region"
            right={
              <Button
                variant="outline"
                size="xs"
                type="button"
                onClick={addOffice}
              >
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
              {...form.register("notes")}
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
    </form>
  );
}
