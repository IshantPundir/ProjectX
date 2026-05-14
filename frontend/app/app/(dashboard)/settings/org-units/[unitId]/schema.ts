import { z } from "zod";

import { TEAM_DEFAULT_ROLES } from "@/lib/api/org-units";

/**
 * Per-unit-type form schemas for the redesigned detail pages.
 *
 * The metadata blob shipped to the backend is a strict subset of the
 * unit's known keys — we never round-trip unknown keys through the
 * frontend, but we also never strip them: each detail component reads
 * `unit.metadata` defensively and merges its own keys on save (see
 * `mergeMetadata` below).
 */

export const unitNameSchema = z
  .string()
  .min(1, "Name is required")
  .max(100, "Name must be 100 characters or fewer");

/* ─── Team ───────────────────────────────────────────────────────────── */
//
// All free-text fields default to "" in the form layer (not undefined) so
// React Hook Form's input/output types align with `z.infer`. Optional
// keys we genuinely persist as `undefined` (locale + compliance overrides)
// stay `.optional()`.

export const teamFormSchema = z.object({
  name: unitNameSchema,
  default_role: z.enum(TEAM_DEFAULT_ROLES).optional(),
  focus: z.string().max(2000),
});
export type TeamFormValues = z.infer<typeof teamFormSchema>;

/* ─── Division ───────────────────────────────────────────────────────── */

export const divisionFormSchema = z.object({
  name: unitNameSchema,
  description: z.string().max(2000),
});
export type DivisionFormValues = z.infer<typeof divisionFormSchema>;

/* ─── Region ─────────────────────────────────────────────────────────── */
//
// Task 8: Region will adopt the country/state/city block (replacing the
// locale + compliance shape below). Schema rewrite deferred until
// RegionDetail is refactored so it doesn't need dozens of @ts-expect-error.
export const regionFormSchema = z.object({
  name: unitNameSchema,
  default_timezone: z.string().optional(),
  default_currency: z.string().optional(),
  default_locale: z.string().optional(),
  compliance_aivia_il: z.boolean().optional(),
  compliance_gdpr_eu: z.boolean().optional(),
  compliance_ccpa_ca: z.boolean().optional(),
});
export type RegionFormValues = z.infer<typeof regionFormSchema>;

/* ─── Company / Client account ───────────────────────────────────────── */
//
// Free-text everywhere. No length caps (backend trims; empty string clears).
// Saving sends explicit `set_<field>: true` sentinels so each field
// persists independently.
export const companyFormSchema = z.object({
  name: unitNameSchema,
  about: z.string(),
  industry: z.string(),
  hiring_bar: z.string(),
  website: z.string(),
  country: z.string(),
  state: z.string(),
  city: z.string(),
});
export type CompanyFormValues = z.infer<typeof companyFormSchema>;

/* ─── Helpers ────────────────────────────────────────────────────────── */

/**
 * Merge a per-page form's metadata payload onto the existing unit
 * metadata, then strip undefined values so the backend treats the result
 * as "explicit set" rather than "set to undefined". Keys present on the
 * existing metadata but absent from `next` are preserved — load-bearing
 * because the backend's `set_metadata: true` is destructive (replaces
 * the entire JSONB blob).
 *
 * Pass `{ key: undefined }` in `next` to explicitly drop a key — used
 * when the user resets an inheritance override.
 */
export function mergeMetadata(
  existing: Record<string, unknown> | null | undefined,
  next: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...(existing ?? {}) };
  for (const [key, value] of Object.entries(next)) {
    if (value === undefined) {
      delete merged[key];
    } else {
      merged[key] = value;
    }
  }
  return merged;
}
