"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import { apiFetch } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import {
  TEAM_DEFAULT_ROLES,
  type OrgUnit,
  type OrgUnitMember,
  type TeamDefaultRole,
  type TeamMetadata,
} from "@/lib/api/org-units";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { useOrgUnitMembers } from "@/lib/hooks/use-org-unit-members";
import { useRoles } from "@/lib/hooks/use-roles";
import { useTeamMembers } from "@/lib/hooks/use-team-members";
import { useAssignRole } from "@/lib/hooks/use-assign-role";
import { useRemoveRole } from "@/lib/hooks/use-remove-role";

import { Sidebar } from "./Sidebar";
import {
  Avatar,
  CrumbBack,
  HeaderActions,
  StatItem,
  StatSep,
  UnitCrumb,
  UnitPill,
  memberInitials,
} from "./shared";
import {
  mergeMetadata,
  teamFormSchema,
  type TeamFormValues,
} from "./schema";

import "./detail.css";

export interface TeamDetailProps {
  unit: OrgUnit;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
  jobsAnchoredHere: { id: string; title: string }[];
  openRolesCount: number;
  onBack: () => void;
  onSaved: (next: OrgUnit) => void;
}

/**
 * Team detail page — 1:1 with the design package's HTML structure.
 *
 * Layout:
 *   <main data-edit-mode="…">
 *     <header class="unit-header">… type pill + default-role popover
 *       + crumb + h1 + stats …</header>
 *     <div class="unit-body">
 *       <div>  // main column
 *         <section class="section">  // Members
 *       </div>
 *       <aside class="sidebar">  // open jobs / hierarchy / governance / delete
 *     </div>
 *   </main>
 */
export function TeamDetail({
  unit,
  parentChain,
  subUnits,
  jobsAnchoredHere,
  openRolesCount,
  onBack,
  onSaved,
}: TeamDetailProps) {
  const metadata = (unit.metadata ?? {}) as TeamMetadata;
  const [mode, setMode] = React.useState<"view" | "edit">("view");
  const [popoverOpen, setPopoverOpen] = React.useState(false);
  const popoverRef = React.useRef<HTMLSpanElement>(null);

  const defaults = React.useMemo<TeamFormValues>(
    () => ({
      name: unit.name,
      default_role: metadata.default_role,
      focus: metadata.focus ?? "",
    }),
    [unit.name, metadata.default_role, metadata.focus],
  );

  const form = useForm<TeamFormValues>({
    resolver: zodResolver(teamFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    form.reset(defaults);
  }, [defaults, form]);

  // Close popover on outside click.
  React.useEffect(() => {
    if (!popoverOpen) return;
    function onClick(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setPopoverOpen(false);
      }
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [popoverOpen]);

  // Close popover when leaving edit mode.
  React.useEffect(() => {
    if (mode !== "edit") setPopoverOpen(false);
  }, [mode]);

  const updateMutation = useUpdateOrgUnit();
  const watched = form.watch();
  const role = watched.default_role;

  async function onSubmit(values: TeamFormValues) {
    try {
      const merged = mergeMetadata(unit.metadata, {
        default_role: values.default_role,
        focus: values.focus?.trim() || undefined,
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
      toast.success("Team saved");
      setMode("view");
      form.reset({
        name: updated.name,
        default_role: (updated.metadata as TeamMetadata | null)?.default_role,
        focus: (updated.metadata as TeamMetadata | null)?.focus ?? "",
      });
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save team");
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
      data-edit-mode={mode === "edit" ? "true" : "false"}
    >
      <header className="unit-header">
        <CrumbBack onBack={onBack} />
        <div className="unit-header-row">
          <div className="unit-header-main">
            <div className="unit-pills">
              <UnitPill type="team" />
              <span
                ref={popoverRef}
                className="default-role-pill editable"
                role="button"
                tabIndex={0}
                aria-haspopup="true"
                aria-expanded={popoverOpen}
                onClick={() => {
                  if (mode === "edit") setPopoverOpen((v) => !v);
                }}
                onKeyDown={(e) => {
                  if (mode === "edit" && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    setPopoverOpen((v) => !v);
                  }
                }}
              >
                <span className="label">Default ·</span>{" "}
                <span>{role ?? "Not set"}</span>
                <span className="caret">▾</span>
                <div
                  className="role-popover"
                  data-open={popoverOpen}
                  role="dialog"
                  aria-label="Change default role"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="role-popover-title">
                    Default role for this team
                  </div>
                  <div
                    className="role-picker"
                    role="radiogroup"
                    aria-label="Default role"
                  >
                    {TEAM_DEFAULT_ROLES.map((r) => {
                      const active = role === r;
                      return (
                        <button
                          key={r}
                          className="role-chip"
                          type="button"
                          role="radio"
                          aria-checked={active}
                          aria-pressed={active}
                          onClick={() => {
                            form.setValue("default_role", r, {
                              shouldDirty: true,
                            });
                          }}
                        >
                          <span className="role-dot" aria-hidden="true" />
                          {r}
                        </button>
                      );
                    })}
                  </div>
                  <div className="role-popover-foot">
                    All members of this team automatically hold this role.
                    Admin is excluded — admins must be selected deliberately.
                  </div>
                </div>
              </span>
            </div>
            <UnitCrumb items={crumbs} />
            <h1
              className="unit-name"
              style={{ marginTop: 8 }}
              data-editable-text="team-name"
              contentEditable={mode === "edit"}
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
            <div className="unit-stats">
              <StatItem value={unit.member_count} label="members" />
              <StatSep />
              <StatItem value={openRolesCount} label="open jobs" />
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
          {/* Focus narrative is editable inline above the members list */}
          {(mode === "edit" || metadata.focus) && (
            <section className="section">
              <div className="section-head">
                <div className="section-head-main">
                  <div className="section-title">Focus</div>
                  <div className="section-sub">
                    Optional. Copilot uses this to tailor JD enrichment for
                    roles anchored to this team.
                  </div>
                </div>
              </div>
              <div className="card card-pad">
                <textarea
                  className="textarea"
                  rows={3}
                  placeholder="e.g. Core libraries, build system, and developer tooling used by every other engineering team."
                  {...form.register("focus")}
                />
              </div>
            </section>
          )}

          <TeamMembersSection
            unit={unit}
            defaultRole={role}
            parentChain={parentChain}
            isEdit={mode === "edit"}
          />
        </div>

        <Sidebar
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          topCard={
            <TeamOpenJobsCard
              jobs={jobsAnchoredHere}
              teamName={unit.name}
            />
          }
        />
      </div>
    </main>
  );
}

/* ─── Open jobs card (sidebar — Team only) ─── */

function TeamOpenJobsCard({
  jobs,
  teamName,
}: {
  jobs: { id: string; title: string }[];
  teamName: string;
}) {
  return (
    <div className="sidebar-card">
      <div className="sidebar-card-title">
        Open jobs <span className="count">· {jobs.length}</span>
      </div>
      {jobs.length === 0 ? (
        <div
          className="empty-state"
          style={{ padding: "12px 0", textAlign: "left" }}
        >
          No active jobs anchored here.
        </div>
      ) : (
        <>
          <div className="sidebar-jobs">
            {jobs.map((j) => (
              <a
                key={j.id}
                className="sidebar-job-row"
                href={`/jobs/${j.id}/review`}
              >
                <div className="sb-job-main">
                  <div className="sb-job-title">{j.title}</div>
                </div>
                <span className="sb-job-arrow" aria-hidden="true">
                  →
                </span>
              </a>
            ))}
          </div>
          <a className="sidebar-see-all" href="/jobs">
            See all jobs scoped to {teamName} →
          </a>
        </>
      )}
    </div>
  );
}

/* ─── Members section (Team's cascading-admin variant) ─── */

interface TeamMembersSectionProps {
  unit: OrgUnit;
  defaultRole: TeamDefaultRole | undefined;
  parentChain: OrgUnit[];
  isEdit: boolean;
}

function TeamMembersSection({
  unit,
  defaultRole,
  parentChain,
  isEdit,
}: TeamMembersSectionProps) {
  const membersQuery = useOrgUnitMembers(unit.id);
  const rolesQuery = useRoles();
  const tenantUsersQuery = useTeamMembers();
  const assignMutation = useAssignRole();
  const removeMutation = useRemoveRole();

  const members = membersQuery.data ?? [];
  const roles = rolesQuery.data ?? [];
  const tenantUsers = (tenantUsersQuery.data ?? []).filter(
    (u) => u.source === "user" && u.is_active,
  );
  const existingIds = new Set(members.map((m) => m.user_id));
  const candidateUsers = tenantUsers.filter((u) => !existingIds.has(u.id));

  const adminRoleId = roles.find((r) => r.name === "Admin")?.id ?? null;
  const defaultRoleId =
    roles.find((r) => r.name === defaultRole)?.id ?? null;

  // Resolve "cascaded from {ancestor}" for each admin row.
  const cascadeSources = useAdminCascadeSources(parentChain, members);

  const [pickerUserId, setPickerUserId] = React.useState("");

  async function handleAdd() {
    if (!pickerUserId || !defaultRoleId) {
      toast.error(
        defaultRole
          ? `Could not resolve role id for "${defaultRole}".`
          : "Set a default role for this team before adding members.",
      );
      return;
    }
    try {
      await assignMutation.mutateAsync({
        unitId: unit.id,
        userId: pickerUserId,
        roleId: defaultRoleId,
      });
      toast.success(`Added with role "${defaultRole}"`);
      setPickerUserId("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add member");
    }
  }

  async function handleRemove(userId: string, roleId: string) {
    try {
      await removeMutation.mutateAsync({ unitId: unit.id, userId, roleId });
      toast.success("Role removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove role");
    }
  }

  async function handleOptIn(userId: string) {
    if (!defaultRoleId) {
      toast.error("Set a default role for this team first.");
      return;
    }
    try {
      await assignMutation.mutateAsync({
        unitId: unit.id,
        userId,
        roleId: defaultRoleId,
      });
      toast.success(`Opted in to "${defaultRole}"`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to opt in");
    }
  }

  return (
    <section className="section">
      <div className="section-head">
        <div className="section-head-main">
          <div className="section-title">
            Members <span className="count">{members.length} people</span>
          </div>
        </div>
      </div>
      <div className="card members-list">
        <div className="add-member-helper">
          No role picker — every member inherits the team&rsquo;s default
          role. Admins shown separately and may opt in to also hold the
          default role.
        </div>
        <div className="members-head team-grid">
          <span>Person</span>
          <span>Role</span>
          <span>Added</span>
          <span />
        </div>

        {membersQuery.isLoading && members.length === 0 && (
          <div className="empty-state">Loading members…</div>
        )}
        {!membersQuery.isLoading && members.length === 0 && (
          <div className="empty-state">No members yet.</div>
        )}

        {members.map((m) => {
          const adminAssignment = adminRoleId
            ? m.roles.find((r) => r.role_id === adminRoleId)
            : m.roles.find((r) => r.role_name === "Admin");
          const defaultAssignment = defaultRoleId
            ? m.roles.find((r) => r.role_id === defaultRoleId)
            : m.roles.find((r) => r.role_name === defaultRole);
          const isAdmin = !!adminAssignment;
          const cascadedFrom = isAdmin
            ? cascadeSources.get(m.user_id) ?? null
            : null;
          const sourceText = isAdmin
            ? cascadedFrom
              ? `cascaded from ${cascadedFrom}`
              : "added manually"
            : null;
          const earliest = [...m.roles].sort((a, b) =>
            a.assigned_at < b.assigned_at ? -1 : 1,
          )[0]?.assigned_at;

          return (
            <div key={m.user_id} className="member-row team-grid">
              <div className="member-id">
                <Avatar
                  name={m.full_name ?? m.email}
                  admin={isAdmin}
                  size={28}
                />
                <div>
                  <div className="member-name">
                    {m.full_name ?? m.email.split("@")[0]}
                  </div>
                  <div className="member-meta">
                    {m.email}
                    {sourceText && (
                      <>
                        {" · "}
                        <span className="source">{sourceText}</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
              <div className="row-pills">
                {isAdmin && <span className="role-pill admin">Admin</span>}
                {defaultAssignment && defaultRole && (
                  <span className="role-pill default">{defaultRole}</span>
                )}
                {!defaultAssignment && !isAdmin && defaultRole && (
                  <span className="role-pill default">{defaultRole}</span>
                )}
                {isAdmin && !defaultAssignment && defaultRole && (
                  <button
                    className="opt-in-link"
                    type="button"
                    onClick={() => handleOptIn(m.user_id)}
                  >
                    + Opt in to {defaultRole}
                  </button>
                )}
              </div>
              <div className="row-meta">
                {earliest ? earliest.slice(0, 7) : "—"}
              </div>
              <button
                className="row-action"
                type="button"
                aria-label={`Remove ${m.full_name ?? m.email}`}
                onClick={() => {
                  // Default action: remove the role most associated with this
                  // row. For admins that's Admin; for others that's the
                  // default role assignment.
                  const assignment = adminAssignment ?? defaultAssignment ?? m.roles[0];
                  if (assignment)
                    handleRemove(m.user_id, assignment.role_id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}

        <div className="add-member-row">
          {!isEdit ? (
            <span style={{ fontSize: 12, color: "var(--px-fg-4)" }}>
              Adding will assign{" "}
              <strong style={{ color: "var(--px-caution)" }}>
                {defaultRole ?? "—"}
              </strong>{" "}
              automatically.
            </span>
          ) : (
            <div className="add-form">
              <select
                className="input"
                style={{ height: 28, fontSize: 12, flex: 1 }}
                value={pickerUserId}
                onChange={(e) => setPickerUserId(e.target.value)}
                aria-label="Pick a person to add"
              >
                <option value="">Pick a person…</option>
                {candidateUsers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name ? `${u.full_name} · ${u.email}` : u.email}
                  </option>
                ))}
              </select>
              <button
                className="btn outline xs"
                type="button"
                onClick={handleAdd}
                disabled={
                  !pickerUserId || !defaultRoleId || assignMutation.isPending
                }
              >
                {assignMutation.isPending ? "Adding…" : "+ Add member"}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Resolve, for each admin on the team, the closest ancestor where the
 * same user holds Admin. Returns a map user_id → ancestor name (null
 * means "added directly to this team"). Tree depth is bounded so the
 * per-ancestor member-list fetch is cheap.
 */
function useAdminCascadeSources(
  parentChain: OrgUnit[],
  members: OrgUnitMember[],
): Map<string, string | null> {
  const [map, setMap] = React.useState<Map<string, string | null>>(
    () => new Map(),
  );
  const memberIds = members
    .filter((m) => m.roles.some((r) => r.role_name === "Admin"))
    .map((m) => m.user_id)
    .sort()
    .join(",");
  const ancestorIds = parentChain.map((u) => u.id).join(",");

  React.useEffect(() => {
    let cancelled = false;
    if (!memberIds || !parentChain.length) {
      setMap(new Map());
      return;
    }
    void (async () => {
      try {
        const token = await getFreshSupabaseToken();
        // Walk closest ancestor first, so the nearest Admin source wins.
        const reversed = [...parentChain].reverse();
        const results = await Promise.all(
          reversed.map((u) =>
            apiFetch<OrgUnitMember[]>(`/api/org-units/${u.id}/members`, {
              token,
            }).catch(() => [] as OrgUnitMember[]),
          ),
        );
        if (cancelled) return;
        const out = new Map<string, string | null>();
        for (const userId of memberIds.split(",")) {
          let source: string | null = null;
          for (let i = 0; i < reversed.length; i++) {
            const ancestor = reversed[i];
            const found = results[i].find((x) => x.user_id === userId);
            if (found && found.roles.some((r) => r.role_name === "Admin")) {
              source = ancestor.name;
              break;
            }
          }
          out.set(userId, source);
        }
        setMap(out);
      } catch {
        if (!cancelled) setMap(new Map());
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [memberIds, ancestorIds, parentChain]);

  return map;
}
