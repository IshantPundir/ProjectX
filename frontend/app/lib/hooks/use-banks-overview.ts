'use client'

import { useQuery } from '@tanstack/react-query'

import {
  questionBanksApi,
  type BanksOverviewResponse,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useBanksOverview(jobId: string) {
  return useQuery<BanksOverviewResponse>({
    queryKey: ['banks', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.listBanks(token, jobId)
    },
    enabled: !!jobId,
    staleTime: 0,
  })
}
