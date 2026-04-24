'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { teamApi, type DeactivateUserResponse } from '@/lib/api/team'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useDeactivateUser() {
  const qc = useQueryClient()
  return useMutation<DeactivateUserResponse, Error, string>({
    mutationFn: async (userId) => {
      const token = await getFreshSupabaseToken()
      return teamApi.deactivate(token, userId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['team', 'members'] })
    },
  })
}
