'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { jobsApi, type JobPostingWithSnapshot, type UpdateJobBody } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * PATCH /api/jobs/{id}. Editable while the job is in 'draft'; server
 * returns 409 once the job has moved past draft.
 *
 * Toast and error mapping are intentionally NOT in this hook — the
 * draft editor surfaces field-level state inline (the 409 case is also
 * meaningful UI on the consumer's side, not a generic toast).
 */
export function useUpdateJobDraft(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<JobPostingWithSnapshot, Error, UpdateJobBody>({
    mutationFn: async (body: UpdateJobBody) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.update(token, jobId, body)
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<JobPostingWithSnapshot>(['jobs', jobId], updated)
      void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
    },
  })
}
