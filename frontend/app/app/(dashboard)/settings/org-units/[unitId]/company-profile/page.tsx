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

type OrgUnit = {
  id: string
  name: string
  unit_type: string
  company_profile: CompanyProfile | null
  company_profile_completed_at: string | null
}

export default function CompanyProfilePage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const queryClient = useQueryClient()

  const { data: unit, isLoading } = useQuery<OrgUnit>({
    queryKey: ['org-unit', unitId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit>(`/api/org-units/${unitId}`, { token })
    },
  })

  const mutation = useMutation({
    mutationFn: async (profile: CompanyProfile) => {
      const token = await getFreshSupabaseToken()
      return apiFetch(`/api/org-units/${unitId}`, {
        token,
        method: 'PATCH',
        body: JSON.stringify({
          set_company_profile: true,
          company_profile: profile,
        }),
      })
    },
    onSuccess: () => {
      toast.success('Company profile saved')
      queryClient.invalidateQueries({ queryKey: ['org-unit', unitId] })
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
