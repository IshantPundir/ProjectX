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
    // Await so WizardShell sees the refetched consented state before the
    // mutation reports success — avoids stranded OtpStep/CameraMicStep
    // transitions when refetch lands after the component callback.
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
