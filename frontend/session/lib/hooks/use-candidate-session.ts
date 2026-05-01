'use client'

import { useQuery } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type PreCheckResponse,
} from '@/lib/api/candidate-session'

export function useCandidateSession(token: string) {
  return useQuery<PreCheckResponse>({
    queryKey: ['candidate-session', token],
    queryFn: () => candidateSessionApi.preCheck(token),
    enabled: !!token,
    retry: false,
  })
}
