'use client'

import { useQuery } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useOrgUnits() {
  return useQuery<OrgUnit[]>({
    queryKey: ['org-units'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.list(token)
    },
    staleTime: 10_000,
  })
}
