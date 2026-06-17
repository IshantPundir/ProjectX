'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * POST /api/jobs/{id}/re-extract-signals. Clears the job's banks, regresses
 * to signals_extracting, and re-dispatches signal extraction. Invalidates job
 * detail, jobs list, and the banks list so stale bank data is cleared.
 */
export function useReExtractSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.reExtractSignals(token, jobId)
    },
    onSuccess: () => {
      toast.success('Re-enriching JD & re-extracting signals')
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
    },
    onError: (error) => {
      toast.error(error.message || 'Failed to re-run extraction')
    },
  })
}
