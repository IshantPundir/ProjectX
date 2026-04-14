import { apiFetch } from './client'
import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

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

export const orgUnitsApi = {
  list: (token: string): Promise<OrgUnit[]> =>
    apiFetch<OrgUnit[]>('/api/org-units', { token }),

  me: (token: string): Promise<MeData> =>
    apiFetch<MeData>('/api/auth/me', { token }),

  create: (
    token: string,
    body: {
      name: string
      unit_type: string
      parent_unit_id: string | null
      company_profile: CompanyProfile | null
    },
  ): Promise<OrgUnit> =>
    apiFetch<OrgUnit>('/api/org-units', {
      method: 'POST',
      token,
      body: JSON.stringify(body),
    }),
}
