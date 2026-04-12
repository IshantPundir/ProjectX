'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type StarterTemplate } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useStarterPack() {
  return useQuery<StarterTemplate[]>({
    queryKey: ['pipeline-starter-pack'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getStarterPack(token)
    },
    staleTime: Infinity, // starter pack is static
  })
}
