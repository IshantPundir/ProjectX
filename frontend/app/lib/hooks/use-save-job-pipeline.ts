'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type JobPipelineInstance,
  type UpdateJobPipelineBody,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useSaveJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, UpdateJobPipelineBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.updateJobPipeline(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Pipeline saved')
      void qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to save pipeline: ${err.message}`),
  })
}

export function useResetJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.resetJobPipeline(token, jobId)
    },
    onSuccess: () => {
      toast.success('Pipeline reset to source template')
      void qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to reset: ${err.message}`),
  })
}
