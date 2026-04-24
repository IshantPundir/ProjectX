'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useDeleteOrgUnit() {
  const qc = useQueryClient()
  return useMutation<{ status: string }, Error, string>({
    mutationFn: async (unitId) => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.delete(token, unitId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['org-units'] })
    },
  })
}
