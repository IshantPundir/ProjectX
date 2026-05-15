'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * POST /api/jobs/{id}/extract-signals — transitions a draft job to
 * signals_extracting and dispatches Phase 2 (signal extraction).
 *
 * 422s ('empty_raw_jd', 'company_profile_incomplete') are surfaced as
 * ApiError on the consumer side; toasts are intentionally NOT in this
 * hook so the JobDraftEditor can render inline banner CTAs with
 * actionable links.
 */
export function useExtractSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.extractSignals(token, jobId)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
  })
}
