'use client'

import { useQuery } from '@tanstack/react-query'

import { teamApi, type TeamMember } from '@/lib/api/team'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useTeamMembers() {
  return useQuery<TeamMember[]>({
    queryKey: ['team', 'members'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return teamApi.list(token, { signal })
    },
    staleTime: 10_000,
  })
}
