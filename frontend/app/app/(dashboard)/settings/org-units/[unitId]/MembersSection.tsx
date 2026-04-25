"use client";

import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";

import { Button, DangerConfirmDialog } from "@/components/px";
import { applyApiErrorToForm } from "@/lib/api/errors";
import { useAssignRole } from "@/lib/hooks/use-assign-role";
import { useOrgUnitMembers } from "@/lib/hooks/use-org-unit-members";
import { useRemoveRole } from "@/lib/hooks/use-remove-role";
import { useRoles } from "@/lib/hooks/use-roles";
import { useTeamMembers } from "@/lib/hooks/use-team-members";
import { Section } from "./shared";

function initials(s: string | null | undefined): string {
  if (!s) return "?";
  return (
    s
      .trim()
      .split(/[\s@]+/)
      .filter(Boolean)
      .map((w) => w[0]?.toUpperCase() ?? "")
      .slice(0, 2)
      .join("") || "?"
  );
}

const assignRoleSchema = z.object({
  user_id: z.string().min(1, "Pick a user"),
  role_id: z.string().min(1, "Pick a role"),
});
type AssignRoleFormValues = z.infer<typeof assignRoleSchema>;

/** Role assignments on a given org unit. Reusable across every unit-type
 * detail page (Company, Division, Region, Team). Lists direct members and
 * exposes an inline "+ Assign role" form. */
export function MembersSection({ unitId }: { unitId: string }) {
  const membersQuery = useOrgUnitMembers(unitId);
  const rolesQuery = useRoles();
  const tenantUsersQuery = useTeamMembers();

  const members = useMemo(() => membersQuery.data ?? [], [membersQuery.data]);
  const roles = rolesQuery.data ?? [];
  const tenantUsers = useMemo(
    () =>
      (tenantUsersQuery.data ?? []).filter(
        (x) => x.source === "user" && x.is_active,
      ),
    [tenantUsersQuery.data],
  );
  const loading = membersQuery.isLoading || rolesQuery.isLoading;

  const [formOpen, setFormOpen] = useState(false);

  const assignForm = useForm<AssignRoleFormValues>({
    resolver: zodResolver(assignRoleSchema),
    defaultValues: { user_id: "", role_id: "" },
  });
  const assignMutation = useAssignRole();

  async function onAssignSubmit(values: AssignRoleFormValues) {
    try {
      await assignMutation.mutateAsync({
        unitId,
        userId: values.user_id,
        roleId: values.role_id,
      });
      assignForm.reset();
      setFormOpen(false);
      toast.success("Role assigned");
    } catch (err) {
      if (applyApiErrorToForm(err, assignForm)) return;
      toast.error(err instanceof Error ? err.message : "Failed to assign role");
    }
  }

  const [toRemove, setToRemove] = useState<{
    userId: string;
    roleId: string;
    roleName: string;
  } | null>(null);
  const removeMutation = useRemoveRole();

  async function handleConfirmRemove() {
    if (!toRemove) return;
    try {
      await removeMutation.mutateAsync({
        unitId,
        userId: toRemove.userId,
        roleId: toRemove.roleId,
      });
      toast.success("Role removed");
      setToRemove(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove role");
      // Keep dialog open so the user can retry or cancel explicitly.
    }
  }

  // Show every tenant user in the assign dropdown — including users with
  // existing roles — so recruiters can add e.g. "Interviewer" to someone
  // who already has "Hiring Manager". Filtering to unassigned users would
  // block that flow.
  const candidateUsers = tenantUsers;

  return (
    <Section title="Members & Roles" sub="Assign users to roles on this org unit. Assignments inherit to descendant units for access / pool gating.">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="text-[12px]" style={{ color: "var(--px-fg-4)" }}>
            {loading
              ? "Loading…"
              : `${members.length} member${members.length === 1 ? "" : "s"}`}
          </div>
          <Button
            size="sm"
            variant={formOpen ? "outline" : "primary"}
            onClick={() => {
              setFormOpen((v) => {
                const next = !v;
                if (!next) assignForm.reset();
                return next;
              });
            }}
          >
            {formOpen ? "Cancel" : "+ Assign role"}
          </Button>
        </div>

        {formOpen && (
          <form
            onSubmit={assignForm.handleSubmit(onAssignSubmit)}
            className="rounded-md border p-3"
            style={{
              background: "var(--px-surface-2)",
              borderColor: "var(--px-hairline)",
            }}
          >
            <div className="grid gap-2.5" style={{ gridTemplateColumns: "1.6fr 1fr auto" }}>
              <select
                className="px-input sm"
                aria-label="User"
                {...assignForm.register("user_id")}
              >
                <option value="">Pick a user…</option>
                {candidateUsers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name ? `${u.full_name} · ${u.email}` : u.email}
                  </option>
                ))}
              </select>
              <select
                className="px-input sm"
                aria-label="Role"
                {...assignForm.register("role_id")}
              >
                <option value="">Pick a role…</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                type="submit"
                disabled={assignMutation.isPending || assignForm.formState.isSubmitting}
              >
                {assignMutation.isPending ? "Assigning…" : "Assign"}
              </Button>
            </div>
            {(assignForm.formState.errors.user_id?.message ||
              assignForm.formState.errors.role_id?.message ||
              assignForm.formState.errors.root?.message) && (
              <p className="mt-2 text-[11px]" style={{ color: "var(--px-danger, #b91c1c)" }}>
                {assignForm.formState.errors.root?.message ||
                  assignForm.formState.errors.user_id?.message ||
                  assignForm.formState.errors.role_id?.message}
              </p>
            )}
            <p className="mt-2 text-[11px]" style={{ color: "var(--px-fg-4)" }}>
              Tip: assign &ldquo;Hiring Manager&rdquo; at the company or division level so the
              role covers every job under it via ancestry.
            </p>
          </form>
        )}

        <div
          className="rounded-md border"
          style={{ borderColor: "var(--px-hairline)" }}
        >
          <div
            className="grid items-center px-3.5 py-2 text-[11px] font-medium uppercase"
            style={{
              gridTemplateColumns: "1.6fr 1.4fr 120px 80px",
              background: "var(--px-surface-2)",
              color: "var(--px-fg-4)",
              borderBottom: "1px solid var(--px-divider)",
            }}
          >
            <span>Person</span>
            <span>Roles</span>
            <span>Assigned</span>
            <span></span>
          </div>
          {members.length === 0 ? (
            <div
              className="px-3.5 py-6 text-center text-[12.5px]"
              style={{ color: "var(--px-fg-4)" }}
            >
              {loading ? "Loading…" : "No direct members on this unit yet."}
            </div>
          ) : (
            members.map((m, i) => (
              <div
                key={m.user_id}
                className="grid items-center px-3.5 py-2.5 text-[13px]"
                style={{
                  gridTemplateColumns: "1.6fr 1.4fr 120px 80px",
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
                <div className="flex flex-wrap gap-1">
                  {m.roles.map((r) => (
                    <span
                      key={r.role_id}
                      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]"
                      style={{
                        background: "var(--px-surface-2)",
                        borderColor: "var(--px-hairline)",
                        color: "var(--px-fg-2)",
                      }}
                    >
                      {r.role_name}
                      <button
                        type="button"
                        aria-label={`Remove ${r.role_name}`}
                        onClick={() =>
                          setToRemove({
                            userId: m.user_id,
                            roleId: r.role_id,
                            roleName: r.role_name,
                          })
                        }
                        className="text-zinc-400 hover:text-red-600"
                        style={{ fontSize: 11, lineHeight: 1 }}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
                <div
                  className="px-mono text-[11.5px]"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  {m.roles[0]
                    ? new Date(m.roles[0].assigned_at)
                        .toISOString()
                        .slice(0, 10)
                    : "—"}
                </div>
                <div></div>
              </div>
            ))
          )}
        </div>
      </div>

      <DangerConfirmDialog
        open={!!toRemove}
        title="Remove role"
        description={
          <>
            Remove <strong>{toRemove?.roleName}</strong> from this user on this unit?
          </>
        }
        confirmLabel="Remove role"
        pendingLabel="Removing…"
        pending={removeMutation.isPending}
        onConfirm={handleConfirmRemove}
        onClose={() => setToRemove(null)}
      />
    </Section>
  );
}
