'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit, type OrgUnitMetadata } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export interface UpdateOrgUnitInput {
  unitId: string
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
