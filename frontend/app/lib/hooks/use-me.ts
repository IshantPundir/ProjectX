'use client'

import { useQuery } from '@tanstack/react-query'

import { authApi, type MeResponse } from '@/lib/api/auth'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useMe() {
  return useQuery<MeResponse>({
    queryKey: ['me'],
    queryFn: async () => authApi.me(await getFreshSupabaseToken()),
    staleTime: 60_000,
  })
}

/**
 * True when the caller can manage membership of the given unit — i.e. is
 * either the tenant super admin or holds the system 'Admin' role on that
 * exact unit (the cascade in backend assign_role guarantees descendants
 * also carry an explicit row, so this exact-match check is sufficient).
 */
export function canManageUnit(
  me: MeResponse | null | undefined,
  unitId: string,
): boolean {
  if (!me) return false
  if (me.is_super_admin) return true
  return me.assignments.some(
    (a) => a.org_unit_id === unitId && a.role_name === 'Admin',
  )
}

/**
 * True when the caller is a tenant super admin OR holds the system
 * 'Admin' role on at least one org unit. Used to gate access to the
 * tenant-wide admin surfaces (Org units, Team & access). Non-admin
 * users (Recruiter / Hiring Manager / Interviewer / Observer) get
 * `false` and should not see the corresponding nav items or pages.
 */
export function isAnyAdmin(
  me: MeResponse | null | undefined,
): boolean {
  if (!me) return false
  if (me.is_super_admin) return true
  return me.assignments.some((a) => a.role_name === 'Admin')
}
