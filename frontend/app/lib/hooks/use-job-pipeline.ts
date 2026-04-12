'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type JobPipelineInstance } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJobPipeline(jobId: string) {
  return useQuery<JobPipelineInstance | null>({
    queryKey: ['job-pipeline', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getJobPipeline(token, jobId)
    },
    enabled: !!jobId,
  })
}
