'use client'

import { useQuery } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useOrgUnit(unitId: string) {
  return useQuery<OrgUnit>({
    queryKey: ['org-units', unitId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.get(token, unitId)
    },
    enabled: !!unitId,
    staleTime: 10_000,
  })
}
