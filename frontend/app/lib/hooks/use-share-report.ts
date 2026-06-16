'use client'

import { useMutation } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { reportsApi } from '@/lib/api/reports'

export function useShareReport(sessionId: string) {
  return useMutation({
    mutationFn: async (recipientEmail: string) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.share(token, sessionId, recipientEmail)
    },
  })
}
