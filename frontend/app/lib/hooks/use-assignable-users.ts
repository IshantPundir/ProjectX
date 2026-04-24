'use client'

import { useQuery } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { pipelinesApi, type AssignableUser, type ParticipantRole } from '@/lib/api/pipelines'

export function useAssignableUsers(jobId: string, role: ParticipantRole | null) {
  return useQuery<AssignableUser[]>({
    queryKey: ['jobs', jobId, 'assignable-users', role],
    enabled: role !== null,
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getAssignableUsers(token, jobId, role as ParticipantRole, { signal })
    },
  })
}
