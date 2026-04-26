'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { pipelinesApi } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useActivateJob(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.activate(token, jobId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs', jobId] })
      qc.invalidateQueries({ queryKey: ['jobs-list'] })
    },
  })
}
