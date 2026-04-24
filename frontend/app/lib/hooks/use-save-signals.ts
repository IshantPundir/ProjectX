'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi, type SaveSignalsBody, type SignalSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useSaveSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<SignalSnapshot, Error, SaveSignalsBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.saveSignals(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Signals saved')
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
    },
    onError: (error) => {
      toast.error(error.message || 'Failed to save signals')
    },
  })
}
