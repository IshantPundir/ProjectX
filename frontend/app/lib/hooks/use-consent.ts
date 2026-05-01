'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type ConsentBody,
  type PreCheckResponse,
} from '@/lib/api/candidate-session'

export function useConsent(token: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, ConsentBody>({
    mutationFn: (body) => candidateSessionApi.consent(token, body),
    // Flip state to 'consented' on the cached /pre-check response
    // synchronously so WizardShell advances on this render tick. Awaiting
    // invalidateQueries alone races with React's subscriber-notify hop and
    // sometimes strands the wizard on ConsentStep until the user reloads.
    // The follow-up invalidation refreshes any other fields.
    onSuccess: () => {
      qc.setQueryData<PreCheckResponse>(
        ['candidate-session', token],
        (old) => (old ? { ...old, state: 'consented' } : old),
      )
      qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
