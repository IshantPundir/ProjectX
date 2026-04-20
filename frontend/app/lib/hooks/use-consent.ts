'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type ConsentBody,
} from '@/lib/api/candidate-session'

export function useConsent(token: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, ConsentBody>({
    mutationFn: (body) => candidateSessionApi.consent(token, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
