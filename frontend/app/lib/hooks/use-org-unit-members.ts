'use client'

import { useQuery } from '@tanstack/react-query'

import { orgUnitsApi, type OrgUnitMember } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useOrgUnitMembers(
  unitId: string,
  options?: { enabled?: boolean },
) {
  const enabled = (options?.enabled ?? true) && !!unitId
  return useQuery<OrgUnitMember[]>({
    queryKey: ['org-units', unitId, 'members'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.listMembers(token, unitId)
    },
    enabled,
    staleTime: 10_000,
  })
}
