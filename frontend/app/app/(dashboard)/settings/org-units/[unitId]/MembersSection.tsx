"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/px";
import { apiFetch } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import {
  orgUnitsApi,
  type OrgUnitMember,
  type RoleOption,
} from "@/lib/api/org-units";
import { Section } from "./shared";

interface TeamMember {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_super_admin: boolean;
  source: string;
  status: string;
  assignments: { org_unit_id: string; org_unit_name: string; role_name: string }[];
  created_at: string;
}

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

/** Role assignments on a given org unit. Reusable across every unit-type
 * detail page (Company, Division, Region, Team). Lists direct members and
 * exposes an inline "+ Assign role" form. */
export function MembersSection({ unitId }: { unitId: string }) {
  const [members, setMembers] = useState<OrgUnitMember[]>([]);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [tenantUsers, setTenantUsers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);

  const [formOpen, setFormOpen] = useState(false);
  const [formUserId, setFormUserId] = useState("");
  const [formRoleId, setFormRoleId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const token = await getFreshSupabaseToken();
      const [m, r, u] = await Promise.all([
        orgUnitsApi.listMembers(token, unitId),
        orgUnitsApi.listRoles(token),
        apiFetch<TeamMember[]>("/api/settings/team/members", { token }),
      ]);
      setMembers(m);
      setRoles(r);
      setTenantUsers(u.filter((x) => x.source === "user" && x.is_active));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load members");
    } finally {
      setLoading(false);
    }
  }, [unitId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  async function handleSubmit() {
    if (!formUserId || !formRoleId) {
      toast.error("Pick a user and a role");
      return;
    }
    setSubmitting(true);
    try {
      const token = await getFreshSupabaseToken();
      await orgUnitsApi.assignRole(token, unitId, {
        user_id: formUserId,
        role_id: formRoleId,
      });
      toast.success("Role assigned");
      setFormOpen(false);
      setFormUserId("");
      setFormRoleId("");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to assign role");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(userId: string, roleId: string, roleName: string) {
    if (!confirm(`Remove ${roleName} from this user on this unit?`)) return;
    try {
      const token = await getFreshSupabaseToken();
      await orgUnitsApi.removeRole(token, unitId, userId, roleId);
      toast.success("Role removed");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove role");
    }
  }

  const candidateUsers = useMemo(
    () =>
      tenantUsers.filter(
        (u) =>
          !members.some((m) => m.user_id === u.id && m.roles.length > 0) ||
          // Still allow picking a user who has another role — useful for adding
          // e.g. "Interviewer" to someone who already has "Hiring Manager".
          true,
      ),
    [tenantUsers, members],
  );

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
            onClick={() => setFormOpen((v) => !v)}
          >
            {formOpen ? "Cancel" : "+ Assign role"}
          </Button>
        </div>

        {formOpen && (
          <div
            className="rounded-md border p-3"
            style={{
              background: "var(--px-surface-2)",
              borderColor: "var(--px-hairline)",
            }}
          >
            <div className="grid gap-2.5" style={{ gridTemplateColumns: "1.6fr 1fr auto" }}>
              <select
                className="px-input sm"
                value={formUserId}
                onChange={(e) => setFormUserId(e.target.value)}
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
                value={formRoleId}
                onChange={(e) => setFormRoleId(e.target.value)}
              >
                <option value="">Pick a role…</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
              <Button size="sm" onClick={handleSubmit} disabled={submitting}>
                {submitting ? "Assigning…" : "Assign"}
              </Button>
            </div>
            <p className="mt-2 text-[11px]" style={{ color: "var(--px-fg-4)" }}>
              Tip: assign &ldquo;Hiring Manager&rdquo; at the company or division level so the
              role covers every job under it via ancestry.
            </p>
          </div>
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
                          handleRemove(m.user_id, r.role_id, r.role_name)
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
    </Section>
  );
}
