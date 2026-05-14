import { apiFetch } from './client'

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
 * Task 8: RegionDetail still references RegionMetadata — remove when
 * RegionDetail refactor lands.
 */
export interface RegionMetadata {
  default_timezone?: string
  default_currency?: string
  default_locale?: string
  compliance_aivia_il?: boolean
  compliance_gdpr_eu?: boolean
  compliance_ccpa_ca?: boolean
}

/**
 * Address inheritance — per-field walk. `values.<field>` is the closest
 * non-null value walking root -> unit. `source_unit_id` is the closest
 * ancestor that contributed at least one field; null means every value
 * came from the unit itself (or nothing is set).
 */
export interface InheritedAddress {
  values: {
    country: string | null
    state: string | null
    city: string | null
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
  // Column-level company-profile fields. All free-text, all nullable.
  about: string | null
  industry: string | null
  hiring_bar: string | null
  website: string | null
  country: string | null
  state: string | null
  city: string | null
  company_profile_completed_at: string | null
  /**
   * Two-state gate for ATS-imported client_account units. Defaults to
   * 'complete' for natively-created units; ATS sync writes 'pending'
   * until a recruiter fills out the company profile, at which point the
   * org-unit PUT handler flips it to 'complete' and unblocks any JDs
   * that were imported while the profile was pending.
   */
  company_profile_completion_status: 'pending' | 'complete'
  metadata: OrgUnitMetadata | null
  inherited_address: InheritedAddress | null
  /** @deprecated Task 8: RegionDetail still reads these — remove when RegionDetail refactor lands. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  inherited_locale?: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  inherited_compliance?: any
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
      about?: string | null
      industry?: string | null
      hiring_bar?: string | null
      website?: string | null
      country?: string | null
      state?: string | null
      city?: string | null
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
      about?: string
      set_about?: boolean
      industry?: string
      set_industry?: boolean
      hiring_bar?: string
      set_hiring_bar?: boolean
      website?: string
      set_website?: boolean
      country?: string
      set_country?: boolean
      state?: string
      set_state?: boolean
      city?: string
      set_city?: boolean
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
