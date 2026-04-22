import { apiFetch } from './client'
import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

/**
 * Rich per-unit-type metadata. Shape is intentionally loose — each unit_type
 * uses its own subset of keys. Unknown keys are preserved by the backend.
 *
 * Company/Client extras: legal_name, short_name, website, sector, hq, size,
 *   description, interview_style, panel_size, takehome_policy, time_to_decision,
 *   values, base_philosophy, equity, bonus, locations[], remote_policy, visa,
 *   contract_start, renews, fee_model, guarantee_period, exclusive_roles,
 *   account_manager.
 * Region: code, primary_city, timezone, currency, locale, offices[], notes.
 * Division: code, lead_name, cost_center, hiring_budget, description,
 *   default_panel, default_takehome, default_tech_screen, bar_raiser_pool.
 * Team: slug, lead_name, focus.
 */
export type OrgUnitMetadata = Record<string, unknown>

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
  metadata: OrgUnitMetadata | null
}

export interface MeData {
  is_super_admin: boolean
  workspace_mode: string
  assignments: {
    org_unit_id: string
    org_unit_name: string
    role_name: string
    permissions: string[]
  }[]
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
    apiFetch(`/api/org-units/${unitId}/members`, {
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
    apiFetch(`/api/org-units/${unitId}/members/${userId}/roles/${roleId}`, {
      method: 'DELETE',
      token,
    }),

  listRoles: (token: string): Promise<RoleOption[]> =>
    apiFetch<RoleOption[]>('/api/roles', { token }),

  get: (token: string, unitId: string): Promise<OrgUnit> =>
    apiFetch<OrgUnit>(`/api/org-units/${unitId}`, { token }),

  me: (token: string): Promise<MeData> =>
    apiFetch<MeData>('/api/auth/me', { token }),

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
}
