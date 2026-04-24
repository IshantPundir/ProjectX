'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit, type OrgUnitMetadata } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

export interface CreateOrgUnitInput {
  name: string
  unit_type: string
  parent_unit_id: string | null
  company_profile: CompanyProfile | null
  metadata?: OrgUnitMetadata | null
}

export function useCreateOrgUnit() {
  const qc = useQueryClient()
  return useMutation<OrgUnit, Error, CreateOrgUnitInput>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.create(token, body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['org-units'] })
    },
  })
}
