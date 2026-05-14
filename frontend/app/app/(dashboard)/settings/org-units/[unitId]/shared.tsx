"use client";

import * as React from "react";

import { Combobox, type ComboboxOption } from "./Combobox";

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

/* ─── Compliance flag row ─── */
// Task 8: RegionDetail still imports — remove when RegionDetail refactor lands.

export interface ComplianceFlagDef {
  name: string;
  desc: string;
}

export function ComplianceRow({
  flag,
  variant,
  isEdit,
  value,
  inheritedValue,
  inheritedFromName,
  onChange,
}: {
  flag: ComplianceFlagDef;
  variant: "source" | "inherit";
  isEdit: boolean;
  /** Override value on this unit. `undefined` = inheriting (only for `variant="inherit"`). */
  value: boolean | undefined;
  inheritedValue: boolean | null;
  inheritedFromName: string | null;
  onChange: (next: boolean | undefined) => void;
}) {
  const isOverridden = value !== undefined;
  const effective = variant === "source" ? !!value : isOverridden ? value : !!inheritedValue;

  if (variant === "source") {
    return (
      <div className="compliance-row">
        <div className="compliance-name">
          {flag.name}
          <span className="desc">{flag.desc}</span>
        </div>
        <div className="compliance-state">
          <input
            type="checkbox"
            className="checkbox"
            checked={!!value}
            disabled={!isEdit}
            onChange={(e) => onChange(e.target.checked)}
            aria-label={`${flag.name} ${value ? "on" : "off"}`}
          />
          <span className={value ? "flag-state-on" : "flag-state-off"}>
            {value ? "On" : "Off"}
          </span>
        </div>
        <span />
      </div>
    );
  }

  return (
    <div className="compliance-row">
      <div className="compliance-name">
        {flag.name}
        <span className="desc">{flag.desc}</span>
      </div>
      <div className="compliance-state">
        {isOverridden ? (
          <>
            <input
              type="checkbox"
              className="checkbox"
              checked={!!effective}
              disabled={!isEdit}
              onChange={(e) => onChange(e.target.checked)}
              aria-label={`${flag.name} ${effective ? "on" : "off"}`}
            />
            <span className={effective ? "flag-state-on" : "flag-state-off"}>
              {effective ? "On · override active" : "Off · override active"}
            </span>
          </>
        ) : (
          <span className={effective ? "flag-state-on" : "flag-state-off"}>
            {effective ? "On" : "Off"}
            {inheritedFromName && ` · inherited from ${inheritedFromName}`}
          </span>
        )}
      </div>
      <button
        className="inherit-toggle"
        type="button"
        aria-pressed={isOverridden}
        disabled={!isEdit}
        onClick={() =>
          isOverridden ? onChange(undefined) : onChange(!!inheritedValue)
        }
      >
        {isOverridden ? "Override active · Reset" : "Override"}
      </button>
    </div>
  );
}

/* ─── Locale chip (3 in a row inside `.locale-strip`) ─── */
// Task 8: RegionDetail still imports LocaleChip, LocaleChipOption, LOCALE_OPTIONS,
// TIMEZONE_OPTIONS, CURRENCY_OPTIONS, CURRENCY_COMMON_VALUES, getLocaleCommonValues,
// getTimezoneCommonValues, localeDefaults — remove all when RegionDetail refactor lands.

export interface LocaleChipOption {
  group?: string;
  value: string;
  label: string;
  /** Right-aligned secondary text in the combobox popover (e.g. "GMT−7"). */
  secondary?: string;
}

export function LocaleChip({
  label,
  variant,
  isEdit,
  value,
  inheritedValue,
  inheritedFromName,
  onChange,
  options,
  commonValues,
}: {
  label: string;
  variant: "source" | "inherit";
  isEdit: boolean;
  value: string | undefined;
  inheritedValue: string | null;
  inheritedFromName: string | null;
  onChange: (next: string | undefined) => void;
  options: LocaleChipOption[];
  /** Optional pinned values surfaced under "Common" in the combobox. */
  commonValues?: string[];
}) {
  const isOverridden = value !== undefined;
  const effective =
    variant === "source"
      ? value ?? ""
      : isOverridden
        ? (value ?? "")
        : (inheritedValue ?? "");

  const stateText =
    variant === "source"
      ? "tenant default"
      : isOverridden
        ? "overridden for this unit"
        : inheritedFromName
          ? `inherited from ${inheritedFromName}`
          : "not set";

  // Compute the "secondary" line live for timezones (UTC offset) and
  // for any other option with the field already set. Locale + currency
  // labels are descriptive on their own; offsets only make sense
  // moment-to-moment, which is why we don't pre-compute them at module
  // load.
  const optionsForCombobox: ComboboxOption[] = React.useMemo(() => {
    return options.map((o) => {
      if (o.secondary) return o;
      if (label === "Timezone" && o.value !== "UTC") {
        return { ...o, secondary: utcOffsetLabel(o.value) };
      }
      return o;
    });
  }, [options, label]);

  // Trigger is editable when the current source/override state allows
  // direct editing. In `source` variant, edit mode unlocks the picker.
  // In `inherit` variant, only an explicit override unlocks it (the
  // override toggle button below seeds the value from inherited first).
  const triggerEditable =
    isEdit && (variant === "source" || isOverridden);

  return (
    <div
      className={`locale-chip${variant === "source" ? " lc-source" : ""}${triggerEditable ? "" : " is-display"}`}
      data-overridden={variant === "source" ? undefined : isOverridden ? "true" : "false"}
    >
      {triggerEditable ? (
        <Combobox
          options={optionsForCombobox}
          value={effective}
          onChange={(next) => onChange(next || undefined)}
          commonValues={commonValues}
          searchPlaceholder={`Search ${label.toLowerCase()}…`}
          ariaLabel={label}
        >
          {({ triggerProps, isOpen }) => (
            <button
              {...triggerProps}
              type="button"
              className={`locale-chip-shell locale-chip-trigger is-editable${isOpen ? " is-open" : ""}`}
            >
              <LocaleChipBody
                label={label}
                value={effective}
                stateText={stateText}
              />
              <span className="lc-caret" aria-hidden="true">
                ▾
              </span>
            </button>
          )}
        </Combobox>
      ) : (
        <div className="locale-chip-shell locale-chip-display">
          <LocaleChipBody
            label={label}
            value={effective}
            stateText={stateText}
          />
        </div>
      )}
      {variant === "inherit" && (
        <button
          className="inherit-toggle lc-toggle"
          type="button"
          aria-pressed={isOverridden}
          disabled={!isEdit}
          onClick={() =>
            isOverridden ? onChange(undefined) : onChange(inheritedValue ?? "")
          }
        >
          {isOverridden ? "Override active · Reset" : "Override"}
        </button>
      )}
    </div>
  );
}

/**
 * Returns the current UTC offset label for an IANA timezone, e.g.
 * "GMT−7" or "GMT+1". Falls back to the empty string on environments
 * without `Intl.DateTimeFormat({timeZoneName:'shortOffset'})` support.
 */
function utcOffsetLabel(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    const found = parts.find((p) => p.type === "timeZoneName")?.value;
    return found ?? "";
  } catch {
    return "";
  }
}

/**
 * Short timezone abbreviation for the current moment, e.g. "IST", "PST".
 * Distinct from the IANA name and the offset; useful as supporting
 * context in the view-mode chip.
 */
function timezoneAbbrev(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "short",
    }).formatToParts(new Date());
    const v = parts.find((p) => p.type === "timeZoneName")?.value ?? "";
    // `short` sometimes returns "GMT+5:30" instead of an abbrev — drop
    // those so we don't show the offset twice.
    return v.startsWith("GMT") || v.startsWith("UTC") ? "" : v;
  } catch {
    return "";
  }
}

/**
 * "Indian Rupee" / "Euro" / "Japanese Yen" — the human-readable
 * currency name in English. Computed lazily; falls back to the code.
 */
let _currencyDisplay: Intl.DisplayNames | null | undefined = undefined;
function currencyName(code: string): string | null {
  if (_currencyDisplay === undefined) {
    try {
      _currencyDisplay = new Intl.DisplayNames(["en"], { type: "currency" });
    } catch {
      _currencyDisplay = null;
    }
  }
  if (!_currencyDisplay) return null;
  const out = _currencyDisplay.of(code);
  return out && out !== code ? out : null;
}

/**
 * Localized currency symbol — "$", "₹", "€" etc. Returns `null` when
 * the runtime can't compute one (treat as "use the code only").
 */
function currencySymbol(code: string): string | null {
  try {
    const parts = new Intl.NumberFormat("en", {
      style: "currency",
      currency: code,
      currencyDisplay: "narrowSymbol",
    }).formatToParts(0);
    const sym = parts.find((p) => p.type === "currency")?.value ?? null;
    return sym && sym !== code ? sym : null;
  } catch {
    return null;
  }
}

/**
 * Resolve language + region names from a BCP 47 locale code.
 *
 *   "en-IN" → { langName: "English", regionName: "India" }
 */
let _langDisplay: Intl.DisplayNames | null | undefined = undefined;
let _regionDisplay: Intl.DisplayNames | null | undefined = undefined;
function localeNames(
  code: string,
): { langName: string | null; regionName: string | null } {
  if (_langDisplay === undefined) {
    try {
      _langDisplay = new Intl.DisplayNames(["en"], { type: "language" });
    } catch {
      _langDisplay = null;
    }
  }
  if (_regionDisplay === undefined) {
    try {
      _regionDisplay = new Intl.DisplayNames(["en"], { type: "region" });
    } catch {
      _regionDisplay = null;
    }
  }
  let langName: string | null = null;
  let regionName: string | null = null;
  try {
    const loc = new Intl.Locale(code);
    if (_langDisplay) {
      const n = _langDisplay.of(loc.language);
      langName = n && n !== loc.language ? n : null;
    }
    if (_regionDisplay && loc.region) {
      const n = _regionDisplay.of(loc.region);
      regionName = n && n !== loc.region ? n : null;
    }
  } catch {
    /* return nulls */
  }
  return { langName, regionName };
}

/* ─── View-mode rich display for the locale chips ─────────────────────
 *
 * In view mode we drop the squashed-button styling and show an icon +
 * label + primary value + supporting line per chip. Edit mode keeps the
 * combobox trigger so the current click target doesn't move.
 */

function ClockIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function CoinIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M15 9.5c0-1.1-1.3-2-3-2s-3 .9-3 2 1.3 2 3 2 3 .9 3 2-1.3 2-3 2-3-.9-3-2" />
      <path d="M12 5.5v13" />
    </svg>
  );
}

function LanguagesIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 8h7" />
      <path d="M9 4v4" />
      <path d="M11 19l4-9 4 9" />
      <path d="M12 17h6" />
      <path d="M3 19c2.5 0 5-2 5-7" />
      <path d="M7 19c-1.5 0-3-1-4-2" />
    </svg>
  );
}

interface LocaleChipDisplayLines {
  primary: string;
  secondary?: string;
}

function describeLocaleValue(
  label: string,
  value: string,
): LocaleChipDisplayLines {
  if (!value) return { primary: "—" };
  if (label === "Timezone") {
    const offset = utcOffsetLabel(value);
    const abbrev = timezoneAbbrev(value);
    const supporting = [offset, abbrev].filter(Boolean).join(" · ");
    return { primary: value, secondary: supporting || undefined };
  }
  if (label === "Currency") {
    const name = currencyName(value);
    const symbol = currencySymbol(value);
    const tail = symbol && symbol !== value ? `${value} · ${symbol}` : value;
    return { primary: name ?? value, secondary: name ? tail : undefined };
  }
  if (label === "Locale") {
    const { langName, regionName } = localeNames(value);
    const human =
      langName && regionName
        ? `${langName} (${regionName})`
        : langName ?? value;
    return {
      primary: human,
      secondary: human === value ? undefined : value,
    };
  }
  return { primary: value };
}

function iconForLabel(label: string): React.ReactNode {
  if (label === "Timezone") return <ClockIcon />;
  if (label === "Currency") return <CoinIcon />;
  if (label === "Locale") return <LanguagesIcon />;
  return null;
}

/**
 * Body content shared by view-mode display and edit-mode trigger. Lifts
 * the icon + stacked-text layout so both wrappers (static `<div>` and
 * clickable `<button>`) render the exact same shape — toggling edit
 * mode no longer shifts dimensions.
 */
function LocaleChipBody({
  label,
  value,
  stateText,
}: {
  label: string;
  value: string;
  stateText: string;
}) {
  const lines = describeLocaleValue(label, value);
  return (
    <>
      <span className="lc-icon" aria-hidden="true">
        {iconForLabel(label)}
      </span>
      <div className="lc-body">
        <div className="lc-label">{label}</div>
        <div className="lc-primary">{lines.primary}</div>
        {lines.secondary && (
          <div className="lc-secondary">{lines.secondary}</div>
        )}
        <div className="lc-state">{stateText}</div>
      </div>
    </>
  );
}

/* ─── Locale option data ──────────────────────────────────────────────
 *
 * Timezones and currencies come from the runtime's built-in
 * `Intl.supportedValuesOf`. That gives us all ~400 IANA tz names and
 * ~300 ISO 4217 currency codes for free, always current with the
 * environment — no library, no manual list to maintain.
 *
 * Locales are different: BCP 47 has no canonical enumeration. We keep
 * a curated list of ~80 commonly-used locales for our customers and
 * format the labels with `Intl.DisplayNames`.
 */

const TIMEZONE_REGION_LABELS: Record<string, string> = {
  Africa: "Africa",
  America: "Americas",
  Antarctica: "Antarctica",
  Arctic: "Arctic",
  Asia: "Asia",
  Atlantic: "Atlantic",
  Australia: "Australia",
  Europe: "Europe",
  Indian: "Indian Ocean",
  Pacific: "Pacific",
  Etc: "Other",
};

interface IntlSupportedValues {
  supportedValuesOf?(key: "timeZone" | "currency"): string[];
}

function safeSupportedValuesOf(key: "timeZone" | "currency"): string[] {
  const fn = (Intl as IntlSupportedValues).supportedValuesOf;
  try {
    return fn ? fn(key) : [];
  } catch {
    return [];
  }
}

function buildTimezoneOptions(): LocaleChipOption[] {
  const tzs = safeSupportedValuesOf("timeZone");
  const out: LocaleChipOption[] = [];
  for (const tz of tzs) {
    if (tz === "UTC") {
      out.push({ value: "UTC", label: "UTC" });
      continue;
    }
    const prefix = tz.split("/")[0];
    out.push({
      group: TIMEZONE_REGION_LABELS[prefix] ?? prefix,
      value: tz,
      label: tz,
    });
  }
  // Stable order: groups emerge by first appearance, items within a
  // group follow IANA's alphabetical order from `supportedValuesOf`.
  return out;
}

function buildCurrencyOptions(): LocaleChipOption[] {
  const codes = safeSupportedValuesOf("currency");
  let displayNames: Intl.DisplayNames | null = null;
  try {
    displayNames = new Intl.DisplayNames(["en"], { type: "currency" });
  } catch {
    displayNames = null;
  }
  return codes.map((code) => {
    const name = displayNames?.of(code);
    return {
      value: code,
      label: name && name !== code ? `${code} — ${name}` : code,
    };
  });
}

/**
 * Curated list of commonly-used BCP 47 locales. Labels are computed at
 * module load using `Intl.DisplayNames` (English UI), so we get
 * "en-US — English (United States)" without hand-maintaining the text.
 *
 * Add a code here to surface it; the label updates automatically.
 */
const CURATED_LOCALE_CODES = [
  // English
  "en-US", "en-CA", "en-GB", "en-AU", "en-NZ", "en-IE", "en-IN", "en-SG", "en-ZA",
  // Spanish
  "es-ES", "es-MX", "es-AR", "es-CL", "es-CO", "es-PE", "es-US",
  // French
  "fr-FR", "fr-CA", "fr-BE", "fr-CH",
  // Portuguese
  "pt-BR", "pt-PT",
  // German
  "de-DE", "de-AT", "de-CH",
  // Italian
  "it-IT", "it-CH",
  // Dutch
  "nl-NL", "nl-BE",
  // Nordic
  "sv-SE", "nb-NO", "da-DK", "fi-FI", "is-IS",
  // Eastern Europe
  "pl-PL", "cs-CZ", "sk-SK", "hu-HU", "ro-RO", "bg-BG", "hr-HR", "sl-SI",
  "el-GR", "uk-UA", "ru-RU", "lt-LT", "lv-LV", "et-EE",
  // East / Southeast Asia
  "ja-JP", "ko-KR", "zh-CN", "zh-TW", "zh-HK", "th-TH", "vi-VN", "id-ID",
  "ms-MY", "fil-PH",
  // South Asia
  "hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "pa-IN", "ur-PK",
  // Middle East / North Africa
  "ar-SA", "ar-AE", "ar-EG", "ar-MA", "he-IL", "tr-TR", "fa-IR",
  // Sub-Saharan Africa
  "af-ZA", "sw-KE", "am-ET",
  // Latin America (other)
  "es-CR", "es-DO", "es-EC", "es-GT", "es-PA", "es-PY", "es-UY", "es-VE",
  // Iberia regional
  "ca-ES", "eu-ES", "gl-ES",
] as const;

function buildLocaleOptions(): LocaleChipOption[] {
  let langNames: Intl.DisplayNames | null = null;
  let regionNames: Intl.DisplayNames | null = null;
  try {
    langNames = new Intl.DisplayNames(["en"], { type: "language" });
    regionNames = new Intl.DisplayNames(["en"], { type: "region" });
  } catch {
    /* fall back to bare codes */
  }

  return CURATED_LOCALE_CODES.map((code) => {
    const [lang, region] = code.split("-");
    const langLabel = langNames?.of(lang) ?? lang;
    const regionLabel = region ? (regionNames?.of(region) ?? region) : null;
    const human = regionLabel
      ? `${langLabel} (${regionLabel})`
      : langLabel;
    return { value: code, label: `${code} — ${human}` };
  });
}

export const TIMEZONE_OPTIONS: LocaleChipOption[] = buildTimezoneOptions();
export const CURRENCY_OPTIONS: LocaleChipOption[] = buildCurrencyOptions();
export const LOCALE_OPTIONS: LocaleChipOption[] = buildLocaleOptions();

/* "Common" suggestions surfaced at the top of each combobox.
 * Browser-resolved values are added when available so the user's likely
 * pick lands as the very first row. */
function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}
function browserLocale(): string | null {
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  try {
    return Intl.DateTimeFormat().resolvedOptions().locale || null;
  } catch {
    return null;
  }
}

export function getTimezoneCommonValues(): string[] {
  const browser = browserTimezone();
  const fallback = [
    "UTC",
    "America/Los_Angeles",
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
  ];
  return browser ? [browser, ...fallback.filter((v) => v !== browser)] : fallback;
}

export const CURRENCY_COMMON_VALUES = ["USD", "EUR", "GBP", "JPY", "CAD"];

export function getLocaleCommonValues(): string[] {
  const browser = browserLocale();
  const fallback = ["en-US", "en-GB", "en-CA"];
  return browser ? [browser, ...fallback.filter((v) => v !== browser)] : fallback;
}

/* ─── Locale-driven defaults for timezone + currency ──────────────────
 *
 * Auto-pre-selecting timezone and currency when the user picks a locale
 * has one tricky failure mode: tzdb canonical aliases drift between
 * runtimes (e.g. `Asia/Kolkata` vs `Asia/Calcutta`,
 * `Europe/Kyiv` vs `Europe/Kiev`). Hardcoding `Asia/Kolkata` while the
 * dropdown comes from `Intl.supportedValuesOf('timeZone')` — which can
 * return `Asia/Calcutta` in older engines — produces a value that
 * doesn't appear in the list.
 *
 * Resolution strategy (in order):
 *   1. Read the runtime's canonical zones via `Intl.Locale.getTimeZones()`
 *      (same source as `supportedValuesOf`, so they always agree).
 *   2. For multi-zone regions, prefer a curated "primary" hint if the
 *      runtime lists it; otherwise fall back to the first zone returned.
 *   3. For engines without `getTimeZones()`, look up the static fallback
 *      and canonicalize via a known-alias table.
 *
 * Currency follows a similar shape but flipped: `getCurrencies()` is in
 * the spec but ships behind `getTimeZones()` (Node ≤25 returns
 * `undefined`), so we use a static region→currency map and validate
 * against `Intl.supportedValuesOf('currency')`.
 */

const SUPPORTED_TIMEZONES = new Set(safeSupportedValuesOf("timeZone"));
const SUPPORTED_CURRENCIES = new Set(safeSupportedValuesOf("currency"));

interface IntlLocaleEnumeration extends Intl.Locale {
  getTimeZones?: () => string[];
  getCurrencies?: () => string[];
}

/**
 * Bidirectional table of tzdb names that drifted between canonical
 * forms. Lets us look up a name a CLDR/IANA update renamed and find
 * whichever spelling the current runtime supports.
 */
const TZ_ALIASES: Record<string, string> = {
  "Asia/Kolkata": "Asia/Calcutta",
  "Asia/Calcutta": "Asia/Kolkata",
  "Europe/Kyiv": "Europe/Kiev",
  "Europe/Kiev": "Europe/Kyiv",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "America/Argentina/Buenos_Aires": "America/Buenos_Aires",
  "Asia/Ho_Chi_Minh": "Asia/Saigon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Europe/Istanbul": "Asia/Istanbul",
  "Asia/Istanbul": "Europe/Istanbul",
};

function canonicalizeTimezone(tz: string | undefined): string | undefined {
  if (!tz) return undefined;
  if (SUPPORTED_TIMEZONES.has(tz)) return tz;
  const alias = TZ_ALIASES[tz];
  return alias && SUPPORTED_TIMEZONES.has(alias) ? alias : undefined;
}

/**
 * For multi-zone regions, the "primary" zone we land on by default.
 * Validated against the runtime list at lookup time — if the hint isn't
 * in the engine's canonical set, we use the first zone the runtime
 * returns instead.
 */
const REGION_PREFERRED_TZ: Record<string, string> = {
  US: "America/New_York",
  CA: "America/Toronto",
  AU: "Australia/Sydney",
  BR: "America/Sao_Paulo",
  RU: "Europe/Moscow",
  AR: "America/Buenos_Aires",
  MX: "America/Mexico_City",
  CN: "Asia/Shanghai",
  ID: "Asia/Jakarta",
  CL: "America/Santiago",
};

/**
 * Static fallback timezone for engines that don't expose
 * `Intl.Locale.getTimeZones()`. Names get canonicalized through
 * `TZ_ALIASES` so they always resolve to a name the runtime supports.
 */
const REGION_FALLBACK_TZ: Record<string, string> = {
  US: "America/New_York", CA: "America/Toronto", MX: "America/Mexico_City",
  GB: "Europe/London", IE: "Europe/Dublin",
  DE: "Europe/Berlin", FR: "Europe/Paris", IT: "Europe/Rome",
  ES: "Europe/Madrid", NL: "Europe/Amsterdam", BE: "Europe/Brussels",
  AT: "Europe/Vienna", PT: "Europe/Lisbon", FI: "Europe/Helsinki",
  GR: "Europe/Athens", HR: "Europe/Zagreb", SI: "Europe/Ljubljana",
  SK: "Europe/Bratislava", LT: "Europe/Vilnius", LV: "Europe/Riga",
  EE: "Europe/Tallinn", CH: "Europe/Zurich",
  SE: "Europe/Stockholm", NO: "Europe/Oslo", DK: "Europe/Copenhagen",
  IS: "Atlantic/Reykjavik",
  PL: "Europe/Warsaw", CZ: "Europe/Prague", HU: "Europe/Budapest",
  RO: "Europe/Bucharest", BG: "Europe/Sofia",
  UA: "Europe/Kyiv", RU: "Europe/Moscow",
  JP: "Asia/Tokyo", KR: "Asia/Seoul", CN: "Asia/Shanghai",
  TW: "Asia/Taipei", HK: "Asia/Hong_Kong",
  SG: "Asia/Singapore", TH: "Asia/Bangkok",
  VN: "Asia/Ho_Chi_Minh", ID: "Asia/Jakarta",
  MY: "Asia/Kuala_Lumpur", PH: "Asia/Manila",
  IN: "Asia/Kolkata", PK: "Asia/Karachi",
  AU: "Australia/Sydney", NZ: "Pacific/Auckland",
  SA: "Asia/Riyadh", AE: "Asia/Dubai", IL: "Asia/Jerusalem",
  TR: "Europe/Istanbul", IR: "Asia/Tehran",
  EG: "Africa/Cairo", MA: "Africa/Casablanca",
  ZA: "Africa/Johannesburg", KE: "Africa/Nairobi",
  ET: "Africa/Addis_Ababa",
  BR: "America/Sao_Paulo", AR: "America/Buenos_Aires",
  CL: "America/Santiago", CO: "America/Bogota",
  PE: "America/Lima", UY: "America/Montevideo",
  VE: "America/Caracas", PY: "America/Asuncion",
  CR: "America/Costa_Rica", PA: "America/Panama",
  GT: "America/Guatemala", EC: "America/Guayaquil",
  DO: "America/Santo_Domingo",
};

/**
 * Region → ISO 4217 currency. ISO 4217 is stable so a static map is
 * fine. Validated against `Intl.supportedValuesOf('currency')` at
 * lookup time so we never hand back a code the dropdown can't render.
 */
const REGION_CURRENCY_FALLBACK: Record<string, string> = {
  US: "USD", CA: "CAD", MX: "MXN",
  GB: "GBP", IE: "EUR",
  DE: "EUR", FR: "EUR", IT: "EUR", ES: "EUR", NL: "EUR", BE: "EUR",
  AT: "EUR", PT: "EUR", FI: "EUR", GR: "EUR", HR: "EUR", SI: "EUR",
  SK: "EUR", LT: "EUR", LV: "EUR", EE: "EUR", CH: "CHF",
  SE: "SEK", NO: "NOK", DK: "DKK", IS: "ISK",
  PL: "PLN", CZ: "CZK", HU: "HUF", RO: "RON", BG: "BGN",
  UA: "UAH", RU: "RUB",
  JP: "JPY", KR: "KRW", CN: "CNY", TW: "TWD", HK: "HKD",
  SG: "SGD", TH: "THB", VN: "VND", ID: "IDR", MY: "MYR", PH: "PHP",
  IN: "INR", PK: "PKR",
  AU: "AUD", NZ: "NZD",
  SA: "SAR", AE: "AED", IL: "ILS", TR: "TRY", IR: "IRR",
  EG: "EGP", MA: "MAD",
  ZA: "ZAR", KE: "KES", ET: "ETB",
  BR: "BRL", AR: "ARS", CL: "CLP", CO: "COP", PE: "PEN",
  UY: "UYU", VE: "VES", PY: "PYG",
  CR: "CRC", PA: "PAB", GT: "GTQ", EC: "USD", DO: "DOP",
};

export interface LocaleDefaults {
  timezone?: string;
  currency?: string;
}

function pickRegionTimezone(
  region: string,
  runtimeZones: readonly string[],
): string | undefined {
  if (runtimeZones.length === 1) {
    return runtimeZones[0];
  }
  if (runtimeZones.length > 1) {
    const hint = REGION_PREFERRED_TZ[region];
    return hint && runtimeZones.includes(hint) ? hint : runtimeZones[0];
  }
  // No runtime API. Fall back to the static map and canonicalize.
  return canonicalizeTimezone(REGION_FALLBACK_TZ[region]);
}

function pickRegionCurrency(
  region: string,
  runtimeCurrencies: readonly string[],
): string | undefined {
  for (const c of runtimeCurrencies) {
    if (SUPPORTED_CURRENCIES.has(c)) return c;
  }
  const fallback = REGION_CURRENCY_FALLBACK[region];
  return fallback && SUPPORTED_CURRENCIES.has(fallback) ? fallback : undefined;
}

/**
 * Resolve sensible timezone + currency defaults for a BCP 47 locale.
 * Always returns values that exist in the runtime's supported lists, so
 * the auto-fill never lands on a stale alias the dropdown can't render.
 */
export function localeDefaults(localeCode: string): LocaleDefaults {
  if (!localeCode) return {};
  let region: string | undefined;
  let runtimeZones: string[] = [];
  let runtimeCurrencies: string[] = [];
  try {
    const loc = new Intl.Locale(localeCode) as IntlLocaleEnumeration;
    region = loc.region;
    runtimeZones = loc.getTimeZones?.() ?? [];
    runtimeCurrencies = loc.getCurrencies?.() ?? [];
  } catch {
    const seg = localeCode.split("-")[1];
    region = seg && /^[A-Z]{2}$/i.test(seg) ? seg.toUpperCase() : undefined;
  }
  if (!region) return {};
  return {
    timezone: pickRegionTimezone(region, runtimeZones),
    currency: pickRegionCurrency(region, runtimeCurrencies),
  };
}

// Task 8: RegionDetail still imports COMPLIANCE_FLAGS — remove when RegionDetail refactor lands.
export const COMPLIANCE_FLAGS: ReadonlyArray<
  ComplianceFlagDef & { key: "compliance_aivia_il" | "compliance_gdpr_eu" | "compliance_ccpa_ca" }
> = [
  {
    key: "compliance_aivia_il",
    name: "AIVIA",
    desc: "AI Video Interview Act — Illinois disclosures",
  },
  {
    key: "compliance_gdpr_eu",
    name: "GDPR",
    desc: "EU data residency & consent flows",
  },
  {
    key: "compliance_ccpa_ca",
    name: "CCPA",
    desc: "California consumer privacy disclosures",
  },
];

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
