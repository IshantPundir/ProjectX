'use client'

import { useQuery } from '@tanstack/react-query'

import {
  candidatesApi,
  type AssignmentResponse,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCandidateAssignments(candidateId: string) {
  return useQuery<AssignmentResponse[]>({
    queryKey: ['candidates', candidateId, 'assignments'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.listAssignments(token, candidateId)
    },
    enabled: !!candidateId,
  })
}
