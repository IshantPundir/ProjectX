'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit, type OrgUnitMetadata } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export interface CreateOrgUnitInput {
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
