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
    // Await the invalidation so the refetched /pre-check response (with
    // otp_verified_at populated) lands before the mutation reports success.
    // Without the await, the component's onSuccess fires before the refetch
    // and WizardShell never re-renders to CameraMicStep until the user
    // manually refreshes.
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
