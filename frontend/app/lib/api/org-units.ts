import { apiFetch } from './client'
import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

/**
 * Rich per-unit-type metadata. Each unit_type uses its own subset of keys;
 * unknown keys are preserved by the backend (JSONB column). The typed
 * subtypes below describe the keys that the redesigned detail pages
 * actively read or write — see spec 2026-04-27 §7.
 */
export type OrgUnitMetadata = Record<string, unknown>

export const TEAM_DEFAULT_ROLES = [
  'Recruiter',
  'Hiring Manager',
  'Interviewer',
  'Observer',
] as const
export type TeamDefaultRole = (typeof TEAM_DEFAULT_ROLES)[number]

export interface TeamMetadata {
  default_role?: TeamDefaultRole
  focus?: string
}

export interface DivisionMetadata {
  description?: string
}

/**
 * Locale + compliance keys can appear on company / client_account (sources
 * of truth) AND on region / client_account (per-field overrides of an
 * inherited value). The frontend uses the same key set in both cases —
 * presence of a key here means "this unit has set/overridden the value".
 */
export interface RegionMetadata {
  default_timezone?: string
  default_currency?: string
  default_locale?: string
  compliance_aivia_il?: boolean
  compliance_gdpr_eu?: boolean
  compliance_ccpa_ca?: boolean
}

export interface CompanyMetadata {
  short_name?: string
  website?: string
  default_timezone?: string
  default_currency?: string
  default_locale?: string
  compliance_aivia_il?: boolean
  compliance_gdpr_eu?: boolean
  compliance_ccpa_ca?: boolean
}

/**
 * Resolved-from-ancestry blocks returned by GET /api/org-units/{id}.
 *
 * `null` (top-level) means no value is set anywhere in the chain — the
 * unit + every ancestor are silent on these keys. `null` per-key means
 * that specific key is unset all the way up. `source_unit_id` points to
 * the closest ancestor that contributed at least one key (used for the
 * "Inherited from {ancestor name}" label in the override UX).
 */
export interface InheritedLocale {
  values: {
    default_timezone: string | null
    default_currency: string | null
    default_locale: string | null
  }
  source_unit_id: string | null
}

export interface InheritedCompliance {
  values: {
    compliance_aivia_il: boolean | null
    compliance_gdpr_eu: boolean | null
    compliance_ccpa_ca: boolean | null
  }
  source_unit_id: string | null
}

export interface OrgUnit {
  id: string
  client_id: string
  parent_unit_id: string | null
  name: string
  unit_type: string
  member_count: number
  created_at: string
  created_by: string | null
  created_by_email: string | null
  deletable_by: string | null
  deletable_by_email: string | null
  admin_delete_disabled: boolean
  is_accessible: boolean
  admin_emails: string[]
  is_root: boolean
  company_profile: CompanyProfile | null
  company_profile_completed_at: string | null
  /**
   * Two-state gate for ATS-imported client_account units. Defaults to
   * 'complete' for natively-created units; ATS sync writes 'pending'
   * until a recruiter fills out the 4-field company profile, at which
   * point the org-unit PUT handler flips it to 'complete' and unblocks
   * any JDs that were imported while the profile was pending.
   */
  company_profile_completion_status: 'pending' | 'complete'
  metadata: OrgUnitMetadata | null
  inherited_locale: InheritedLocale | null
  inherited_compliance: InheritedCompliance | null
}

export interface OrgUnitMember {
  user_id: string
  email: string
  full_name: string | null
  roles: { role_id: string; role_name: string; assigned_at: string }[]
}

export interface RoleOption {
  id: string
  name: string
  description: string
  permissions: string[]
  is_system: boolean
}

export const orgUnitsApi = {
  list: (token: string): Promise<OrgUnit[]> =>
    apiFetch<OrgUnit[]>('/api/org-units', { token }),

  listMembers: (token: string, unitId: string): Promise<OrgUnitMember[]> =>
    apiFetch<OrgUnitMember[]>(`/api/org-units/${unitId}/members`, { token }),

  assignRole: (
    token: string,
    unitId: string,
    body: { user_id: string; role_id: string },
  ): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(`/api/org-units/${unitId}/members`, {
      method: 'POST',
      token,
      body: JSON.stringify(body),
    }),

  removeRole: (
    token: string,
    unitId: string,
    userId: string,
    roleId: string,
  ): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(
      `/api/org-units/${unitId}/members/${userId}/roles/${roleId}`,
      { method: 'DELETE', token },
    ),

  listRoles: (token: string): Promise<RoleOption[]> =>
    apiFetch<RoleOption[]>('/api/roles', { token }),

  get: (token: string, unitId: string): Promise<OrgUnit> =>
    apiFetch<OrgUnit>(`/api/org-units/${unitId}`, { token }),

  create: (
    token: string,
    body: {
      name: string
      unit_type: string
      parent_unit_id: string | null
      company_profile: CompanyProfile | null
      metadata?: OrgUnitMetadata | null
    },
  ): Promise<OrgUnit> =>
    apiFetch<OrgUnit>('/api/org-units', {
      method: 'POST',
      token,
      body: JSON.stringify(body),
    }),

  update: (
    token: string,
    unitId: string,
    body: {
      name?: string
      company_profile?: CompanyProfile | null
      set_company_profile?: boolean
      metadata?: OrgUnitMetadata | null
      set_metadata?: boolean
    },
  ): Promise<OrgUnit> =>
    apiFetch<OrgUnit>(`/api/org-units/${unitId}`, {
      method: 'PUT',
      token,
      body: JSON.stringify(body),
    }),

  delete: (
    token: string,
    unitId: string,
  ): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(`/api/org-units/${unitId}`, {
      method: 'DELETE',
      token,
    }),

  removeMember: (
    token: string,
    unitId: string,
    userId: string,
  ): Promise<{ status: string; count: string }> =>
    apiFetch<{ status: string; count: string }>(
      `/api/org-units/${unitId}/members/${userId}`,
      { method: 'DELETE', token },
    ),
}
