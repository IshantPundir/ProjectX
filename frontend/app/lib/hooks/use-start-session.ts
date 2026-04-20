'use client'

import { useMutation } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type StartSessionPendingResponse,
} from '@/lib/api/candidate-session'

export function useStartSession(token: string) {
  return useMutation<StartSessionPendingResponse, Error, void>({
    mutationFn: () => candidateSessionApi.start(token),
  })
}
