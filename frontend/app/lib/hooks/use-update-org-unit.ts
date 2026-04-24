'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit, type OrgUnitMetadata } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

export interface UpdateOrgUnitInput {
  unitId: string
  body: {
    name?: string
    company_profile?: CompanyProfile | null
    set_company_profile?: boolean
    metadata?: OrgUnitMetadata | null
    set_metadata?: boolean
  }
}

export function useUpdateOrgUnit() {
  const qc = useQueryClient()
  return useMutation<OrgUnit, Error, UpdateOrgUnitInput>({
    mutationFn: async ({ unitId, body }) => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.update(token, unitId, body)
    },
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ['org-units'] })
      void qc.invalidateQueries({ queryKey: ['org-units', updated.id] })
    },
  })
}
