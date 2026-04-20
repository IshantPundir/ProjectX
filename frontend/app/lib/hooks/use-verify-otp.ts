'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type VerifyOtpBody,
} from '@/lib/api/candidate-session'

export function useVerifyOtp(token: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, VerifyOtpBody>({
    mutationFn: (body) => candidateSessionApi.verifyOtp(token, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
