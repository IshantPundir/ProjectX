'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export interface RemoveRoleInput {
  unitId: string
  userId: string
  roleId: string
}

export function useRemoveRole() {
  const qc = useQueryClient()
  return useMutation<{ status: string }, Error, RemoveRoleInput>({
    mutationFn: async ({ unitId, userId, roleId }) => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.removeRole(token, unitId, userId, roleId)
    },
    onSuccess: (_data, { unitId }) => {
      void qc.invalidateQueries({ queryKey: ['org-units', unitId, 'members'] })
    },
  })
}
