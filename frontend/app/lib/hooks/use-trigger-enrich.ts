'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export type UseTriggerEnrichOptions = {
  /**
   * When true (default), the hook fires success / error toasts on its
   * own. When false, the consumer surfaces both — used by JobDraftEditor
   * which renders inline error banners for the two 422 cases
   * (empty_raw_jd, company_profile_incomplete).
   */
  toasts?: boolean
  /** Override the success toast string (only when toasts=true). */
  successMessage?: string
}

/**
 * POST /api/jobs/{id}/enrich. Server picks the right actor (enrich_jd on
 * draft, reenrich_jd on signals_extracted/confirmed). Lifecycle status
 * is unchanged; only enrichment_status + description_enriched move.
 */
export function useTriggerEnrich(
  jobId: string,
  { toasts = true, successMessage = 'Re-enrichment started' }: UseTriggerEnrichOptions = {},
) {
  const queryClient = useQueryClient()

  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.triggerEnrich(token, jobId)
    },
    onSuccess: () => {
      if (toasts) toast.success(successMessage)
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (error) => {
      if (toasts) toast.error(error.message || 'Failed to start re-enrichment')
    },
  })
}
