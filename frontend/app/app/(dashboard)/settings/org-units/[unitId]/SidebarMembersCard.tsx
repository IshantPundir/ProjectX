"use client";

import * as React from "react";
import { toast } from "sonner";

import { DangerConfirmDialog } from "@/components/px";
import { useAssignRole } from "@/lib/hooks/use-assign-role";
import { useOrgUnitMembers } from "@/lib/hooks/use-org-unit-members";
import { useRemoveRole } from "@/lib/hooks/use-remove-role";
import { useRoles } from "@/lib/hooks/use-roles";
import { useTeamMembers } from "@/lib/hooks/use-team-members";

import { Avatar } from "./shared";

export interface SidebarMembersCardProps {
  unitId: string;
  helperText: string;
  isEdit: boolean;
  /** Optional title override — defaults to "Direct members". */
  title?: string;
}

/**
 * Sidebar Direct-members card. Used by Division / Region / Company /
 * Client account pages.
 *
 * Edit-mode capabilities (per spec D5):
 *   - Add a tenant user to this unit with a role
 *   - Add an additional role to an existing member (same picker)
 *   - Remove a single role from a member via the × on its chip
 *   - Removing a member's last role removes them from the unit (the
 *     backend handles this; we just refetch and the row disappears)
 *
 * View mode hides the × buttons and the add affordance via CSS
 * (`[data-edit-mode='false']` on the page <main>).
 */
export function SidebarMembersCard({
  unitId,
  helperText,
  isEdit,
  title = "Direct members",
}: SidebarMembersCardProps) {
  const membersQuery = useOrgUnitMembers(unitId);
  const rolesQuery = useRoles();
  const tenantUsersQuery = useTeamMembers();
  const assignMutation = useAssignRole();
  const removeMutation = useRemoveRole();

  const members = membersQuery.data ?? [];
  const roles = rolesQuery.data ?? [];
  const tenantUsers = (tenantUsersQuery.data ?? []).filter(
    (u) => u.source === "user" && u.is_active,
  );
  // Don't filter existing members out — we want to be able to add an
  // additional role to someone who already holds one on this unit.
  const candidateUsers = tenantUsers;

  const [adding, setAdding] = React.useState(false);
  const [pickerUserId, setPickerUserId] = React.useState("");
  const [pickerRoleId, setPickerRoleId] = React.useState("");

  const [toRemove, setToRemove] = React.useState<{
    userId: string;
    userName: string;
    roleId: string;
    roleName: string;
  } | null>(null);

  async function handleAdd() {
    if (!pickerUserId || !pickerRoleId) return;
    try {
      await assignMutation.mutateAsync({
        unitId,
        userId: pickerUserId,
        roleId: pickerRoleId,
      });
      toast.success("Role assigned");
      setPickerUserId("");
      setPickerRoleId("");
      setAdding(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to assign role");
    }
  }

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
    }
  }

  return (
    <div className="sidebar-card">
      <div className="sidebar-card-title">
        {title} <span className="count">· {members.length}</span>
      </div>
      <div className="sidebar-members">
        {helperText && <div className="helper">{helperText}</div>}
        {members.length === 0 && !membersQuery.isLoading && (
          <div
            className="empty-state"
            style={{ padding: "16px 0", textAlign: "left" }}
          >
            No direct members yet.
          </div>
        )}
        {members.map((m) => {
          const adminRole = m.roles.find((r) => r.role_name === "Admin");
          // Stable display order: Admin first, others alphabetical.
          const otherRoles = m.roles
            .filter((r) => r.role_name !== "Admin")
            .sort((a, b) => a.role_name.localeCompare(b.role_name));
          return (
            <div key={m.user_id} className="sidebar-member-row">
              <Avatar
                name={m.full_name ?? m.email}
                admin={!!adminRole}
                size={26}
              />
              <div className="who">
                <div className="who-name">
                  {m.full_name ?? m.email.split("@")[0]}
                </div>
                <div className="sidebar-role-chips">
                  {adminRole && (
                    <RoleMini
                      label="Admin"
                      tone="admin"
                      onRemove={() =>
                        setToRemove({
                          userId: m.user_id,
                          userName: m.full_name ?? m.email,
                          roleId: adminRole.role_id,
                          roleName: adminRole.role_name,
                        })
                      }
                    />
                  )}
                  {otherRoles.map((r) => (
                    <RoleMini
                      key={r.role_id}
                      label={r.role_name}
                      tone="muted"
                      onRemove={() =>
                        setToRemove({
                          userId: m.user_id,
                          userName: m.full_name ?? m.email,
                          roleId: r.role_id,
                          roleName: r.role_name,
                        })
                      }
                    />
                  ))}
                </div>
              </div>
            </div>
          );
        })}
        {isEdit && !adding && (
          <div className="sidebar-add-row">
            <span style={{ fontSize: "11.5px", color: "var(--px-fg-4)" }}>
              Add a member or extra role.
            </span>
            <button
              className="btn outline xs"
              type="button"
              onClick={() => setAdding(true)}
            >
              + Add
            </button>
          </div>
        )}
        {isEdit && adding && (
          <div className="sidebar-add-form">
            <label className="sidebar-add-form-label">User</label>
            <select
              className="input"
              style={{ height: 28, fontSize: 12 }}
              value={pickerUserId}
              onChange={(e) => setPickerUserId(e.target.value)}
              aria-label="User"
            >
              <option value="">Pick a person…</option>
              {candidateUsers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name ? `${u.full_name} · ${u.email}` : u.email}
                </option>
              ))}
            </select>
            <label className="sidebar-add-form-label">Role</label>
            <select
              className="input"
              style={{ height: 28, fontSize: 12 }}
              value={pickerRoleId}
              onChange={(e) => setPickerRoleId(e.target.value)}
              aria-label="Role"
            >
              <option value="">Pick a role…</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <div className="sidebar-add-form-actions">
              <button
                className="btn primary xs"
                type="button"
                onClick={handleAdd}
                disabled={
                  !pickerUserId || !pickerRoleId || assignMutation.isPending
                }
              >
                {assignMutation.isPending ? "Adding…" : "Assign"}
              </button>
              <button
                className="btn ghost xs"
                type="button"
                onClick={() => {
                  setAdding(false);
                  setPickerUserId("");
                  setPickerRoleId("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
      <DangerConfirmDialog
        open={!!toRemove}
        title="Remove role"
        description={
          <>
            Remove <strong>{toRemove?.roleName}</strong> from{" "}
            <strong>{toRemove?.userName}</strong>? If this is their only role
            on this unit, they&rsquo;ll be removed from it entirely.
          </>
        }
        confirmLabel="Remove role"
        pendingLabel="Removing…"
        pending={removeMutation.isPending}
        onConfirm={handleConfirmRemove}
        onClose={() => setToRemove(null)}
      />
    </div>
  );
}

function RoleMini({
  label,
  tone,
  onRemove,
}: {
  label: string;
  tone: "admin" | "muted";
  onRemove: () => void;
}) {
  return (
    <span className={`role-mini${tone === "admin" ? " is-admin" : ""}`}>
      {label}
      <button
        type="button"
        className="role-mini-remove"
        aria-label={`Remove ${label}`}
        onClick={onRemove}
      >
        ×
      </button>
    </span>
  );
}
