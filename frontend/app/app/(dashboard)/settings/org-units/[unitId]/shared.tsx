"use client";

import * as React from "react";
import Link from "next/link";
import type { OrgUnit } from "@/lib/api/org-units";

/* ─── Unit-type pill ─── */

const UNIT_TYPE_PILL: Record<
  string,
  { label: string; fg: string; bg: string; bd: string }
> = {
  company: {
    label: "Company",
    fg: "var(--px-accent)",
    bg: "var(--px-accent-tint)",
    bd: "var(--px-accent-line)",
  },
  client_account: {
    label: "Client account",
    fg: "var(--px-human)",
    bg: "var(--px-human-bg)",
    bd: "var(--px-human-line)",
  },
  region: {
    label: "Region",
    fg: "var(--px-ai)",
    bg: "var(--px-ai-bg)",
    bd: "var(--px-ai-line)",
  },
  division: {
    label: "Division",
    fg: "var(--px-ok)",
    bg: "var(--px-ok-bg)",
    bd: "var(--px-ok-line)",
  },
  team: {
    label: "Team",
    fg: "var(--px-caution)",
    bg: "var(--px-caution-bg)",
    bd: "var(--px-caution-line)",
  },
};

export function UnitTypePill({ type }: { type: string }) {
  const m = UNIT_TYPE_PILL[type] ?? UNIT_TYPE_PILL.team;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-semibold uppercase"
      style={{
        padding: "3px 10px",
        letterSpacing: "0.6px",
        color: m.fg,
        background: m.bg,
        borderColor: m.bd,
      }}
    >
      <span
        className="h-[6px] w-[6px] rounded-full"
        style={{ background: "currentColor" }}
      />
      {m.label}
    </span>
  );
}

/* ─── Page header ─── */

export interface UnitPageHeaderProps {
  type: string;
  name: string;
  parentPath?: string | null;
  lead?: string | null;
  people?: number | null;
  openRoles?: number | null;
  right?: React.ReactNode;
  onBack?: () => void;
}

export function UnitPageHeader({
  type,
  name,
  parentPath,
  lead,
  people,
  openRoles,
  right,
  onBack,
}: UnitPageHeaderProps) {
  return (
    <div
      className="flex flex-col gap-4 border-b px-8 pb-5 pt-6"
      style={{ borderColor: "var(--px-hairline)" }}
    >
      {onBack && (
        <div>
          <button
            onClick={onBack}
            className="inline-flex items-center gap-1.5 text-[12px] transition-colors"
            style={{ color: "var(--px-fg-3)" }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
            </svg>
            Org units
          </button>
        </div>
      )}
      <div className="flex items-end gap-5">
        <div className="min-w-0 flex-1">
          <div className="mb-2.5 flex items-center gap-2.5">
            <UnitTypePill type={type} />
            {parentPath && (
              <span
                className="px-mono text-[12px]"
                style={{ color: "var(--px-fg-4)" }}
              >
                {parentPath}
              </span>
            )}
          </div>
          <h1
            className="px-serif m-0 text-[34px] font-normal"
            style={{
              letterSpacing: "-0.6px",
              color: "var(--px-fg)",
              lineHeight: 1.05,
            }}
          >
            {name}
          </h1>
          <div
            className="mt-2 flex flex-wrap gap-3.5 text-[13px]"
            style={{ color: "var(--px-fg-3)" }}
          >
            {lead && (
              <span>
                Led by{" "}
                <span
                  style={{ color: "var(--px-fg)", fontWeight: 500 }}
                >
                  {lead}
                </span>
              </span>
            )}
            {people != null && <span>{people} people</span>}
            {openRoles != null && (
              <span>
                {openRoles} open role{openRoles === 1 ? "" : "s"}
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2">{right}</div>
      </div>
    </div>
  );
}

/* ─── Section ─── */

export function Section({
  title,
  sub,
  right,
  children,
}: {
  title: string;
  sub?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="mb-2.5 flex items-end gap-3">
        <div className="min-w-0 flex-1">
          <div
            className="text-[11px] font-semibold uppercase"
            style={{ letterSpacing: "1.1px", color: "var(--px-fg-4)" }}
          >
            {title}
          </div>
          {sub && (
            <div
              className="mt-1 text-[12.5px]"
              style={{ color: "var(--px-fg-3)" }}
            >
              {sub}
            </div>
          )}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

/* ─── Field ─── */

export function Field({
  label,
  hint,
  children,
  span = 1,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  span?: 1 | 2 | 3;
}) {
  return (
    <div style={{ gridColumn: `span ${span}` }}>
      <label className="px-label">{label}</label>
      {children}
      {hint && <div className="px-hint">{hint}</div>}
    </div>
  );
}

/* ─── Tag chip ─── */

export function TagChip({
  text,
  tone = "soft",
  onRemove,
}: {
  text: string;
  tone?: "soft" | "accent" | "human";
  onRemove?: () => void;
}) {
  const tones = {
    soft: {
      bg: "var(--px-surface-2)",
      fg: "var(--px-fg-2)",
      bd: "var(--px-hairline-strong)",
    },
    accent: {
      bg: "var(--px-accent-tint)",
      fg: "var(--px-accent)",
      bd: "var(--px-accent-line)",
    },
    human: {
      bg: "var(--px-human-bg)",
      fg: "var(--px-human)",
      bd: "var(--px-human-line)",
    },
  }[tone];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border text-[12px] font-medium"
      style={{
        padding: "4px 10px",
        background: tones.bg,
        color: tones.fg,
        borderColor: tones.bd,
      }}
    >
      {text}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="opacity-60 hover:opacity-100"
          aria-label={`Remove ${text}`}
        >
          ×
        </button>
      )}
    </span>
  );
}

/* ─── Sub-units list ─── */

const TYPE_DOT: Record<string, string> = {
  company: "var(--px-accent)",
  client_account: "var(--px-human)",
  region: "var(--px-ai)",
  division: "var(--px-ok)",
  team: "var(--px-caution)",
};

export function SubUnitsList({
  subUnits,
  onAdd,
}: {
  subUnits: OrgUnit[];
  onAdd?: () => void;
}) {
  return (
    <div
      className="overflow-hidden rounded-[10px] border"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div
        className="flex items-center border-b px-3.5 py-3"
        style={{ borderColor: "var(--px-hairline)" }}
      >
        <span
          className="flex-1 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: "1px", color: "var(--px-fg-4)" }}
        >
          Sub-units · {subUnits.length}
        </span>
        {onAdd && (
          <button type="button" onClick={onAdd} className="px-btn ghost xs">
            + Add
          </button>
        )}
      </div>
      {subUnits.length === 0 ? (
        <div
          className="px-3.5 py-4 text-[12px]"
          style={{ color: "var(--px-fg-4)" }}
        >
          None yet.
        </div>
      ) : (
        subUnits.map((u, i) => (
          <Link
            key={u.id}
            href={`/settings/org-units/${u.id}`}
            className="flex cursor-pointer items-center gap-2.5 px-3.5 py-2.5 text-[12.5px] transition-colors"
            style={{
              borderBottom:
                i < subUnits.length - 1
                  ? "1px solid var(--px-divider)"
                  : "none",
              color: "var(--px-fg)",
            }}
          >
            <span
              className="h-[6px] w-[6px] rounded-full"
              style={{ background: TYPE_DOT[u.unit_type] ?? "var(--px-fg-4)" }}
            />
            <span className="flex-1">{u.name}</span>
            <span
              className="px-mono text-[11px]"
              style={{
                color: "var(--px-fg-4)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {u.member_count}
            </span>
          </Link>
        ))
      )}
    </div>
  );
}

/* ─── Small stats card ─── */

export function SmallStats({
  rows,
}: {
  rows: { l: string; v: string; ok?: boolean }[];
}) {
  return (
    <div
      className="rounded-[10px] border p-4"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div
        className="mb-3 text-[11px] font-semibold uppercase"
        style={{ letterSpacing: "1px", color: "var(--px-fg-4)" }}
      >
        At a glance
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        {rows.map((r) => (
          <div key={r.l}>
            <div
              className="text-[10.5px] uppercase"
              style={{ letterSpacing: "0.3px", color: "var(--px-fg-4)" }}
            >
              {r.l}
            </div>
            <div
              className="px-mono mt-0.5 text-[22px] font-medium"
              style={{
                color: r.ok ? "var(--px-ok)" : "var(--px-fg)",
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.1,
              }}
            >
              {r.v}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
