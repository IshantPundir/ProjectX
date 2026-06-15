'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type PreCheckResponse,
  type VerifyOtpBody,
} from '@/lib/api/candidate-session'

export function useVerifyOtp(token: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, VerifyOtpBody>({
    mutationFn: (body) => candidateSessionApi.verifyOtp(token, body),
    // Stamp otp_verified_at on the cached /pre-check response synchronously
    // so WizardShell advances to the Ready stage on this render tick. Awaiting
    // invalidateQueries alone races with React's subscriber-notify hop and
    // sometimes leaves the wizard stuck on the Verify stage until the user reloads.
    // The follow-up invalidation refreshes any other fields the server may
    // have changed.
    onSuccess: () => {
      qc.setQueryData<PreCheckResponse>(
        ['candidate-session', token],
        (old) =>
          old ? { ...old, otp_verified_at: new Date().toISOString() } : old,
      )
      qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
