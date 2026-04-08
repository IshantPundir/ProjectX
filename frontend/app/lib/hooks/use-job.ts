'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingWithSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJob(jobId: string) {
  return useQuery<JobPostingWithSnapshot>({
    queryKey: ['jobs', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.get(token, jobId)
    },
    enabled: !!jobId,
    staleTime: 5_000,
  })
}
