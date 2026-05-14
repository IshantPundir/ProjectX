'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { notFound, useParams } from 'next/navigation'
import { toast } from 'sonner'

import {
  CompanyProfileForm,
  type CompanyProfile,
} from '@/components/dashboard/company-profile-form'
import { apiFetch } from '@/lib/api/client'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

// Task 10: This deep-editor route is deprecated. company_profile is removed
// from the backend response; the local type retains it as unknown so this
// file compiles until Task 10 cleanup removes the route entirely.
type OrgUnit = {
  id: string
  name: string
  unit_type: string
  company_profile?: CompanyProfile | null
}

export default function CompanyProfilePage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const queryClient = useQueryClient()

  const { data: unit, isLoading } = useQuery<OrgUnit>({
    queryKey: ['org-unit', unitId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit>(`/api/org-units/${unitId}`, { token, signal })
    },
  })

  /**
   * PUT /api/org-units/{id} returns `unblocked_job_count > 0` when this
   * save flipped company_profile_completion_status pending → complete
   * AND there were JDs sitting in `blocked_pending_client_setup` for
   * this org_unit. The backend already kicked off extraction for each
   * unblocked JD; surface a secondary toast so the recruiter understands
   * the chain reaction.
   */
  type ProfileSaveResponse = {
    unblocked_job_count?: number
  }

  const mutation = useMutation<ProfileSaveResponse, Error, CompanyProfile>({
    mutationFn: async (profile: CompanyProfile) => {
      const token = await getFreshSupabaseToken()
      return apiFetch<ProfileSaveResponse>(`/api/org-units/${unitId}`, {
        token,
        method: 'PUT',
        body: JSON.stringify({
          set_company_profile: true,
          company_profile: profile,
        }),
      })
    },
    onSuccess: (response) => {
      toast.success('Company profile saved')
      const n = response.unblocked_job_count ?? 0
      if (n > 0) {
        toast.info(
          `${n} job${n === 1 ? '' : 's'} queued for processing.`,
        )
      }
      queryClient.invalidateQueries({ queryKey: ['org-unit', unitId] })
      // The unblock cascade transitions JDs out of
      // 'blocked_pending_client_setup' on the backend; refresh the jobs
      // list so the recruiter sees the updated status next time they
      // navigate to /jobs.
      queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err: Error) => {
      toast.error(`Save failed: ${err.message}`)
    },
  })

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading…</div>
  }
  if (!unit) {
    return notFound()
  }

  // Tab is only valid for company / client_account units
  if (!['company', 'client_account'].includes(unit.unit_type)) {
    return (
      <div className="max-w-2xl">
        <Link
          href={`/settings/org-units/${unitId}`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Back to {unit.name}
        </Link>
        <div className="mt-4 text-sm text-zinc-500">
          Company Profile is only configurable on company and client_account units.
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-2xl">
      <Link
        href={`/settings/org-units/${unitId}`}
        className="text-sm text-blue-600 hover:underline"
      >
        ← Back to {unit.name}
      </Link>
      <h2 className="text-lg font-semibold mt-4 mb-2">Company Profile</h2>
      <p className="text-sm text-zinc-500 mb-6">
        Four questions about your company. Required before creating job descriptions.
      </p>
      <CompanyProfileForm
        initialValue={unit.company_profile ?? undefined}
        onSubmit={async (value) => {
          await mutation.mutateAsync(value)
        }}
      />
    </div>
  )
}
