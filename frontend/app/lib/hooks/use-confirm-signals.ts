'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useConfirmSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<JobPostingSummary, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.confirmSignals(token, jobId)
    },
    onSuccess: () => {
      toast.success('Signals confirmed')
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (error) => {
      toast.error(error.message || 'Failed to confirm signals')
    },
  })
}
