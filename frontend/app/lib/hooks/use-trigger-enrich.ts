'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useTriggerEnrich(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.triggerEnrich(token, jobId)
    },
    onSuccess: () => {
      toast.success('Re-enrichment started')
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (error) => {
      toast.error(error.message || 'Failed to start re-enrichment')
    },
  })
}
