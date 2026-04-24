"use client";

import Link from "next/link";
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

const divisionDetailSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),
  code: z.string(),
  lead_name: z.string(),
  cost_center: z.string(),
  hiring_budget: z.string(),
  description: z.string(),
  default_panel: z.string(),
  default_takehome: z.string(),
  default_tech_screen: z.string(),
  bar_raiser_pool: z.string(),
});

type DivisionDetailFormValues = z.infer<typeof divisionDetailSchema>;

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

  const form = useForm<DivisionDetailFormValues>({
    resolver: zodResolver(divisionDetailSchema),
    defaultValues: {
      name: unit.name,
      code: initial.code ?? "",
      lead_name: initial.lead_name ?? "",
      cost_center: initial.cost_center ?? "",
      hiring_budget: initial.hiring_budget ?? "",
      description: initial.description ?? "",
      default_panel: initial.default_panel ?? "",
      default_takehome: initial.default_takehome ?? "",
      default_tech_screen: initial.default_tech_screen ?? "",
      bar_raiser_pool: initial.bar_raiser_pool ?? "",
    },
  });

  const updateMutation = useUpdateOrgUnit();

  // Watch fields used in the render chrome (header lead name).
  const leadName = form.watch("lead_name");

  async function onSubmit(values: DivisionDetailFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          metadata: {
            code: values.code,
            lead_name: values.lead_name,
            cost_center: values.cost_center,
            hiring_budget: values.hiring_budget,
            description: values.description,
            default_panel: values.default_panel,
            default_takehome: values.default_takehome,
            default_tech_screen: values.default_tech_screen,
            bar_raiser_pool: values.bar_raiser_pool,
          },
          set_metadata: true,
        },
      });
      onSaved(updated);
      toast.success("Division saved");
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(
        err instanceof Error ? err.message : "Failed to save division",
      );
    }
  }

  const teams = subUnits.filter((u) => u.unit_type === "team");

  return (
    <div>
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
            <Button variant="ghost" size="sm" type="button">
              Archive
            </Button>
            <Button variant="outline" size="sm" type="button">
              Copy link
            </Button>
            <Button
              size="sm"
              type="button"
              onClick={form.handleSubmit(onSubmit)}
              disabled={updateMutation.isPending}
            >
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
          <Section title="Division details">
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Division name">
                <input className="px-input" {...form.register("name")} />
              </Field>
              <Field label="Code">
                <input
                  className="px-input mono"
                  {...form.register("code", {
                    setValueAs: (v: unknown) =>
                      typeof v === "string" ? v.toUpperCase() : "",
                  })}
                  placeholder="ENG"
                />
              </Field>
              <Field label="Division lead">
                <input
                  className="px-input"
                  {...form.register("lead_name")}
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
                  {...form.register("cost_center")}
                  placeholder="CC-401-ENG"
                />
              </Field>
              <Field label="Hiring budget">
                <input
                  className="px-input mono"
                  {...form.register("hiring_budget")}
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
              {...form.register("description")}
              placeholder="Engineering owns the platform, the product surface customers touch, and the infra that keeps both running…"
            />
          </Section>

          <Section
            title={`Teams under ${unit.name}`}
            right={
              <Button variant="outline" size="xs" type="button">
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
                  {...form.register("default_panel")}
                  placeholder="1 HM · 2 peers · 1 bar raiser"
                />
              </Field>
              <Field label="Takehome">
                <input
                  className="px-input"
                  {...form.register("default_takehome")}
                  placeholder="Off by default"
                />
              </Field>
              <Field label="Technical screen">
                <input
                  className="px-input"
                  {...form.register("default_tech_screen")}
                  placeholder="System-design (45 min)"
                />
              </Field>
              <Field label="Bar raiser pool">
                <input
                  className="px-input"
                  {...form.register("bar_raiser_pool")}
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
    </div>
  );
}
