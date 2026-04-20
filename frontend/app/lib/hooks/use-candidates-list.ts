'use client'

import { useQuery } from '@tanstack/react-query'

import {
  candidatesApi,
  type CandidateListPage,
  type CandidatesListFilters,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCandidatesList(filters: CandidatesListFilters = {}) {
  return useQuery<CandidateListPage>({
    queryKey: ['candidates-list', filters],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.list(token, filters)
    },
  })
}
