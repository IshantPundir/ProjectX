'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidatesApi,
  type AssignmentResponse,
  type AssignmentStatus,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

interface UpdateAssignmentStatusVars {
  assignmentId: string
  status: AssignmentStatus
  jobPostingId?: string
}

export function useUpdateAssignmentStatus(candidateId: string) {
  const queryClient = useQueryClient()

  return useMutation<AssignmentResponse, Error, UpdateAssignmentStatusVars>({
    mutationFn: async ({ assignmentId, status }) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.updateAssignmentStatus(
        token,
        candidateId,
        assignmentId,
        { status },
      )
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId],
      })
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId, 'assignments'],
      })
      void queryClient.invalidateQueries({ queryKey: ['candidates-list'] })
      if (vars.jobPostingId) {
        void queryClient.invalidateQueries({
          queryKey: ['candidates-kanban', vars.jobPostingId],
        })
      }
    },
  })
}
