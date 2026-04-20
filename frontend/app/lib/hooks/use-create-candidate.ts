'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidatesApi,
  type CandidateCreate,
  type CandidateResponse,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateCandidate() {
  const queryClient = useQueryClient()

  return useMutation<CandidateResponse, Error, CandidateCreate>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.create(token, body)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['candidates-list'] })
    },
  })
}
