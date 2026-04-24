'use client'

import { useQuery } from '@tanstack/react-query'

import {
  candidatesApi,
  type CandidateResponse,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCandidate(id: string) {
  return useQuery<CandidateResponse>({
    queryKey: ['candidates', id],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.get(token, id, { signal })
    },
    enabled: !!id,
  })
}
