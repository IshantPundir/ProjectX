'use client'

import { useQuery } from '@tanstack/react-query'

import { orgUnitsApi, type RoleOption } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useRoles() {
  return useQuery<RoleOption[]>({
    queryKey: ['roles'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.listRoles(token)
    },
    // Roles are effectively static within a session
    staleTime: 5 * 60_000,
  })
}
