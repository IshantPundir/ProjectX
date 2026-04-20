'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidatesApi,
  type AssignmentCreate,
  type AssignmentResponse,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateAssignment(candidateId: string) {
  const queryClient = useQueryClient()

  return useMutation<AssignmentResponse, Error, AssignmentCreate>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.createAssignment(token, candidateId, body)
    },
    onSuccess: (_data, body) => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId],
      })
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId, 'assignments'],
      })
      void queryClient.invalidateQueries({
        queryKey: ['candidates-kanban', body.job_posting_id],
      })
      void queryClient.invalidateQueries({ queryKey: ['candidates-list'] })
    },
  })
}
