"use client";

import * as React from "react";


/**
 * Tiny stateless presentation primitives shared by every detail page.
 *
 * The actual layout chrome (`<header class="unit-header">`, `<main>`,
 * `.unit-body`, etc.) is rendered inline by each detail page so the
 * markup matches the design package's HTML structure 1:1. These helpers
 * just factor the small repeating pieces (back link, type pill, save
 * actions, locale chip, compliance row) so we keep the design's
 * vocabulary intact in the React tree.
 */

/* ─── Back link ─── */

export function CrumbBack({ onBack }: { onBack: () => void }) {
  return (
    <button className="crumb-back" type="button" onClick={onBack}>
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
      </svg>
      Org units
    </button>
  );
}

/* ─── Type pill (matches the design's `.unit-pill .pill-{type}`) ─── */

const PILL_LABEL: Record<string, string> = {
  company: "Company",
  client_account: "Client account",
  region: "Region",
  division: "Division",
  team: "Team",
};

export function UnitPill({ type, label }: { type: string; label?: string }) {
  return (
    <span className={`unit-pill pill-${type}`}>
      <span className="dot" aria-hidden="true" />
      {label ?? PILL_LABEL[type] ?? "Unit"}
    </span>
  );
}

/* ─── Edit toggle + Save/Discard pair ─── */

export function HeaderActions({
  mode,
  onModeChange,
  saving,
  dirty,
  onSave,
  onDiscard,
}: {
  mode: "view" | "edit";
  onModeChange: (next: "view" | "edit") => void;
  saving?: boolean;
  dirty?: boolean;
  onSave?: () => void;
  onDiscard?: () => void;
}) {
  const isEdit = mode === "edit";
  // View mode: a single "Edit" toggle. Edit mode: Discard (reset + exit)
  // and Save changes — Discard subsumes the old "Done editing" so the
  // header stays at two buttons in either state.
  if (!isEdit) {
    return (
      <div className="unit-actions">
        <button
          className="edit-toggle"
          type="button"
          aria-pressed={false}
          onClick={() => onModeChange("edit")}
        >
          <span className="dot" aria-hidden="true" />
          <span className="label">Edit</span>
        </button>
      </div>
    );
  }
  return (
    <div className="unit-actions">
      <button
        className="btn ghost sm save-changes-btn"
        type="button"
        onClick={onDiscard}
        disabled={saving}
      >
        Discard
      </button>
      <button
        className="btn primary sm save-changes-btn"
        type="button"
        onClick={onSave}
        disabled={!dirty || saving}
      >
        {saving ? "Saving…" : "Save changes"}
      </button>
    </div>
  );
}

/* ─── Crumb path ("Acme / AMER / Engineering") ─── */

export interface CrumbItem {
  label: string;
  href?: string;
}

export function UnitCrumb({ items }: { items: CrumbItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="unit-crumb">
      {items.map((it, i) => (
        <React.Fragment key={`${it.label}-${i}`}>
          {i > 0 && <span className="sep">/</span>}
          {it.href ? <a href={it.href}>{it.label}</a> : <span>{it.label}</span>}
        </React.Fragment>
      ))}
    </div>
  );
}

/* ─── Stats row helpers ─── */

export function StatItem({
  value,
  label,
  rolledUp,
}: {
  value: number | string;
  label: string;
  rolledUp?: boolean;
}) {
  return (
    <span>
      <span className="stat-num">{value}</span> {label}
      {rolledUp && <span className="stat-rolled"> (rolled up)</span>}
    </span>
  );
}

export function StatSep() {
  return (
    <span className="sep" aria-hidden="true">
      ·
    </span>
  );
}

/* ─── Sub-unit card (matches `.subunit-card`) ─── */

import type {
  OrgUnit,
  TeamDefaultRole,
} from "@/lib/api/org-units";

export function SubUnitCard({
  unit,
  href,
  openRoles = 0,
}: {
  unit: OrgUnit;
  href: string;
  openRoles?: number;
}) {
  const meta = (unit.metadata ?? {}) as { default_role?: TeamDefaultRole };
  const defaultRole = unit.unit_type === "team" ? meta.default_role : undefined;
  return (
    <a className="subunit-card" href={href}>
      <div className="subunit-card-head">
        <span className="subunit-name">{unit.name}</span>
        <span className={`subunit-card-pill pill-${unit.unit_type}`}>
          <span className="dot" aria-hidden="true" />
          {PILL_LABEL[unit.unit_type] ?? "Unit"}
        </span>
      </div>
      <div className="subunit-meta">
        <span>
          <span className="num">{unit.member_count}</span>
          {unit.member_count === 1 ? "member" : "members"}
        </span>
        {defaultRole && (
          <span className="default-role-pill">
            <span className="label">Default ·</span> {defaultRole}
          </span>
        )}
        {openRoles > 0 && <span className="open-pill">{openRoles} open</span>}
        <span className="subunit-arrow">Open →</span>
      </div>
    </a>
  );
}

/* ─── Address chip (3 in a row inside `.address-block`) ─── */

export function AddressChip({
  label,
  isEdit,
  value,
  inheritedValue,
  inheritedFromName,
  onChange,
}: {
  label: string;
  isEdit: boolean;
  value: string | null | undefined;
  inheritedValue: string | null;
  inheritedFromName: string | null;
  onChange: (next: string | null) => void;
}) {
  const display = value || inheritedValue || "";
  const isInherited = !value && !!inheritedValue;
  if (isEdit) {
    return (
      <label className="address-chip address-chip--edit">
        <span className="address-chip-label">{label}</span>
        <input
          className="input address-chip-input"
          value={value ?? ""}
          placeholder={inheritedValue ?? `e.g. ${label}`}
          onChange={(e) => onChange(e.target.value || null)}
        />
      </label>
    );
  }
  return (
    <div className="address-chip">
      <span className="address-chip-label">{label}</span>
      <span className="address-chip-value">{display || "—"}</span>
      {isInherited && inheritedFromName && (
        <span className="address-chip-source">
          Inherited from {inheritedFromName}
        </span>
      )}
    </div>
  );
}

/* ─── Initials avatar ─── */

export function memberInitials(s: string | null | undefined): string {
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

export function Avatar({
  name,
  admin = false,
  size = 28,
}: {
  name: string | null | undefined;
  admin?: boolean;
  size?: 26 | 28;
}) {
  return (
    <div
      className={`avatar${admin ? " admin" : ""}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {memberInitials(name)}
    </div>
  );
}
