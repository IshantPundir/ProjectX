'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type CreateJobPipelineBody,
  type JobPipelineInstance,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, CreateJobPipelineBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.createJobPipeline(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Pipeline created')
      void qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to create pipeline: ${err.message}`),
  })
}
