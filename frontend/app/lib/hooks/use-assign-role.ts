'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { orgUnitsApi } from '@/lib/api/org-units'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export interface AssignRoleInput {
  unitId: string
  userId: string
  roleId: string
}

export function useAssignRole() {
  const qc = useQueryClient()
  return useMutation<{ status: string }, Error, AssignRoleInput>({
    mutationFn: async ({ unitId, userId, roleId }) => {
      const token = await getFreshSupabaseToken()
      return orgUnitsApi.assignRole(token, unitId, {
        user_id: userId,
        role_id: roleId,
      })
    },
    onSuccess: (_data, { unitId }) => {
      void qc.invalidateQueries({ queryKey: ['org-units', unitId, 'members'] })
    },
  })
}
