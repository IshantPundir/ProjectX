'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type PipelineTemplate } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function usePipelineTemplates(unitId: string) {
  return useQuery<PipelineTemplate[]>({
    queryKey: ['pipeline-templates', unitId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.listTemplates(token, unitId)
    },
    enabled: !!unitId,
  })
}
