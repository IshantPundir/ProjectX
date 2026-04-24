'use client'

import { useQuery } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { schedulerApi, type SessionListPage } from '@/lib/api/scheduler'

export function useAssignmentSessions(assignmentId: string) {
  return useQuery<SessionListPage>({
    queryKey: ['assignment-sessions', assignmentId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.listSessions(token, { assignment_id: assignmentId }, { signal })
    },
    enabled: !!assignmentId,
  })
}
