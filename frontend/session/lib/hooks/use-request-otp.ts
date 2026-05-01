'use client'

import { useMutation } from '@tanstack/react-query'

import { candidateSessionApi } from '@/lib/api/candidate-session'

export function useRequestOtp(token: string) {
  return useMutation<void, Error, void>({
    mutationFn: () => candidateSessionApi.requestOtp(token),
  })
}
