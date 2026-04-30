'use client'

import { useMutation } from '@tanstack/react-query'

import {
  candidateSessionApi,
  type StartSessionResponse,
} from '@/lib/api/candidate-session'

export function useStartSession(token: string) {
  return useMutation<StartSessionResponse, Error, void>({
    mutationFn: () => candidateSessionApi.start(token),
  })
}
