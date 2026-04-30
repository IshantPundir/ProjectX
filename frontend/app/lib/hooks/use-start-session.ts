'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type PreCheckResponse,
  type StartSessionResponse,
} from '@/lib/api/candidate-session'

export function useStartSession(token: string) {
  const qc = useQueryClient()
  return useMutation<StartSessionResponse, Error, void>({
    mutationFn: () => candidateSessionApi.start(token),
    // Flip state to 'active' on the cached /pre-check response synchronously
    // so WizardShell can branch into <LiveSessionShell> on this render tick.
    // Awaiting invalidateQueries alone races with React's subscriber-notify
    // hop -- the wizard would otherwise stay on StartStep until the refetch
    // settles, leaving the candidate looking at a stale "Start interview"
    // button after the backend has already moved them to active. The
    // follow-up invalidation refreshes any other fields.
    onSuccess: () => {
      qc.setQueryData<PreCheckResponse>(
        ['candidate-session', token],
        (old) => (old ? { ...old, state: 'active' } : old),
      )
      qc.invalidateQueries({ queryKey: ['candidate-session', token] })
    },
  })
}
