'use client'

import { useQuery } from '@tanstack/react-query'

import {
  questionBanksApi,
  type BankWithQuestionsResponse,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useBankWithQuestions(jobId: string, stageId: string | null) {
  return useQuery<BankWithQuestionsResponse>({
    queryKey: ['bank', jobId, stageId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.getBank(token, jobId, stageId!, { signal })
    },
    enabled: !!jobId && !!stageId,
    staleTime: 0,
  })
}
