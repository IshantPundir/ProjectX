'use client'

import { useMutation } from '@tanstack/react-query'

import { pipelinesApi, type PreviewChangesResponse, type PipelineStageUpdateInput } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function usePipelineClassify(jobId: string) {
  return useMutation<PreviewChangesResponse, Error, { stages: PipelineStageUpdateInput[] }>({
    mutationFn: async (proposed) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.previewChanges(token, jobId, proposed)
    },
  })
}
