"use client";

import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { apiFetch } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
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

interface TeamMetadata {
  slug?: string;
  lead_name?: string;
  focus?: string;
}

interface OrgUnitMember {
  user_id: string;
  email: string;
  full_name: string | null;
  roles: { role_id: string; role_name: string; assigned_at: string }[];
}

const teamDetailSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),
  slug: z.string(),
  lead_name: z.string(),
  focus: z.string(),
});

type TeamDetailFormValues = z.infer<typeof teamDetailSchema>;

function initials(s: string | null | undefined): string {
  if (!s) return "?";
  return s
    .trim()
    .split(/[\s@]+/)
    .filter(Boolean)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("") || "?";
}

export function TeamDetail({
  unit,
  parentPath,
  onBack,
  onSaved,
  openRolesCount,
}: {
  unit: OrgUnit;
  parentPath: string;
  onBack: () => void;
  onSaved: (unit: OrgUnit) => void;
  openRolesCount: number;
}) {
  const metadata = (unit.metadata ?? {}) as TeamMetadata;

  const form = useForm<TeamDetailFormValues>({
    resolver: zodResolver(teamDetailSchema),
    defaultValues: {
      name: unit.name,
      slug: metadata.slug ?? "",
      lead_name: metadata.lead_name ?? "",
      focus: metadata.focus ?? "",
    },
  });

  const updateMutation = useUpdateOrgUnit();

  // Watch lead_name for header chrome.
  const leadName = form.watch("lead_name");

  // `members` is async data (not form state) — keep as-is.
  const [members, setMembers] = useState<OrgUnitMember[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getFreshSupabaseToken();
        const res = await apiFetch<OrgUnitMember[]>(
          `/api/org-units/${unit.id}/members`,
          { token },
        );
        if (!cancelled) setMembers(res);
      } catch {
        /* silent; members section shows empty */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [unit.id]);

  const interviewerCount = useMemo(
    () =>
      members.filter((m) =>
        m.roles.some(
          (r) => r.role_name === "Interviewer" || r.role_name === "Admin",
        ),
      ).length,
    [members],
  );

  async function onSubmit(values: TeamDetailFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          metadata: {
            slug: values.slug,
            lead_name: values.lead_name,
            focus: values.focus,
          },
          set_metadata: true,
        },
      });
      onSaved(updated);
      toast.success("Team saved");
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save team");
    }
  }

  return (
    <div>
      <UnitPageHeader
        type="team"
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
          <Section title="Team details">
            <div className="grid grid-cols-2 gap-3.5">
              <Field label="Team name">
                <input className="px-input" {...form.register("name")} />
              </Field>
              <Field label="Slug" hint="Used in URLs and @mentions.">
                <input
                  className="px-input mono"
                  {...form.register("slug")}
                  placeholder="platform"
                />
              </Field>
              <Field label="Team lead">
                <input
                  className="px-input"
                  {...form.register("lead_name")}
                  placeholder="Alex Chen · Staff Engineer"
                />
              </Field>
              <Field label="Rolls up to">
                <input
                  className="px-input"
                  value={parentPath}
                  readOnly
                />
              </Field>
            </div>
            <div className="mt-3.5">
              <Field
                label="What this team focuses on"
                hint="Optional. Copilot uses this to tailor interview questions for team-specific roles."
              >
                <textarea
                  className="px-input"
                  rows={2}
                  {...form.register("focus")}
                  placeholder="Core libraries, build system, and developer tooling used by every other engineering team."
                />
              </Field>
            </div>
          </Section>

          <MembersSection unitId={unit.id} />

          <Section
            title="Roster summary"
            sub={`${members.length} ${members.length === 1 ? "person" : "people"} · ${interviewerCount} with interview permission`}
          >
            <div
              className="overflow-hidden rounded-[10px] border"
              style={{
                background: "var(--px-surface)",
                borderColor: "var(--px-hairline)",
              }}
            >
              <div
                className="grid items-center px-3.5 py-2 text-[10.5px] font-semibold uppercase"
                style={{
                  gridTemplateColumns: "1.6fr 1fr 1fr 100px",
                  letterSpacing: "1px",
                  color: "var(--px-fg-4)",
                  background: "var(--px-bg-2)",
                  borderBottom: "1px solid var(--px-hairline)",
                }}
              >
                <span>Person</span>
                <span>Role</span>
                <span>Permission</span>
                <span>Added</span>
              </div>
              {members.length === 0 ? (
                <div
                  className="px-3.5 py-6 text-center text-[12.5px]"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  No members yet.
                </div>
              ) : (
                members.map((m, i) => {
                  const permRole =
                    m.roles.find((r) => r.role_name === "Admin") ??
                    m.roles.find((r) => r.role_name === "Interviewer") ??
                    m.roles[0];
                  const isAdmin = permRole?.role_name === "Admin";
                  return (
                    <div
                      key={m.user_id}
                      className="grid items-center px-3.5 py-2.5 text-[13px]"
                      style={{
                        gridTemplateColumns: "1.6fr 1fr 1fr 100px",
                        borderBottom:
                          i < members.length - 1
                            ? "1px solid var(--px-divider)"
                            : "none",
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <div
                          className="flex h-[26px] w-[26px] flex-shrink-0 items-center justify-center rounded-full text-[10.5px] font-semibold"
                          style={{
                            background: "var(--px-accent-tint)",
                            color: "var(--px-accent)",
                          }}
                        >
                          {initials(m.full_name ?? m.email)}
                        </div>
                        <div className="min-w-0">
                          <div
                            className="truncate font-medium"
                            style={{ color: "var(--px-fg)" }}
                          >
                            {m.full_name ?? m.email.split("@")[0]}
                          </div>
                          <div
                            className="px-mono truncate text-[11.5px]"
                            style={{ color: "var(--px-fg-4)" }}
                          >
                            {m.email}
                          </div>
                        </div>
                      </div>
                      <div style={{ color: "var(--px-fg-2)" }}>
                        {m.roles
                          .filter((r) => r.role_name !== "Admin")
                          .map((r) => r.role_name)
                          .join(", ") || "—"}
                      </div>
                      <div>
                        <span
                          className="rounded-full border px-2 py-0.5 text-[11px] font-medium"
                          style={{
                            background: isAdmin
                              ? "var(--px-accent-tint)"
                              : "var(--px-surface-2)",
                            color: isAdmin
                              ? "var(--px-accent)"
                              : "var(--px-fg-3)",
                            borderColor: isAdmin
                              ? "var(--px-accent-line)"
                              : "var(--px-hairline)",
                          }}
                        >
                          {permRole?.role_name ?? "Member"}
                        </span>
                      </div>
                      <div
                        className="px-mono text-[11.5px]"
                        style={{ color: "var(--px-fg-4)" }}
                      >
                        {permRole
                          ? new Date(permRole.assigned_at)
                              .toISOString()
                              .slice(0, 7)
                          : "—"}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Section>
        </div>

        <aside className="flex flex-col gap-3.5">
          <SmallStats
            rows={[
              { l: "Members", v: String(unit.member_count) },
              { l: "Interviewers", v: String(interviewerCount) },
              {
                l: "Open roles",
                v: String(openRolesCount),
                ok: openRolesCount > 0,
              },
              {
                l: "Pressure",
                v:
                  openRolesCount >= 3
                    ? "Hot"
                    : openRolesCount > 0
                      ? "Steady"
                      : "Cool",
              },
            ]}
          />
        </aside>
      </div>
    </div>
  );
}
