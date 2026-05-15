'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm, type Resolver } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button, Input, Label } from '@/components/px'
import { apiFetch } from '@/lib/api/client'
import { jobsApi } from '@/lib/api/jobs'
import type {
  EmploymentType,
  WorkArrangement,
  SalaryCurrency,
  TravelRequired,
  StartDatePref,
} from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * /jobs/new — basics only.
 *
 * Per the unified job-creation flow (docs/superpowers/specs/2026-05-14-
 * unified-job-creation-flow-design.md), this page collects identity and
 * meta only. After submit, the recruiter lands on /jobs/{id} where they
 * paste the raw JD and explicitly trigger enrichment + signal extraction.
 *
 * ATS-imported jobs skip this page entirely — they land directly in
 * `draft` with raw JD pre-filled and the recruiter takes over from
 * /jobs/{id}, indistinguishable from a manually-created job.
 */

const EMPLOYMENT_TYPE_OPTIONS: { value: EmploymentType; label: string }[] = [
  { value: 'full_time', label: 'Full time' },
  { value: 'part_time', label: 'Part time' },
  { value: 'contract', label: 'Contract' },
  { value: 'contract_to_hire', label: 'Contract to hire' },
  { value: 'internship', label: 'Internship' },
]

const WORK_ARRANGEMENT_OPTIONS: { value: WorkArrangement; label: string }[] = [
  { value: 'onsite', label: 'Onsite' },
  { value: 'remote', label: 'Remote' },
  { value: 'hybrid', label: 'Hybrid' },
]

const SALARY_CURRENCY_OPTIONS: { value: SalaryCurrency; label: string }[] = [
  { value: 'INR', label: 'INR' },
  { value: 'USD', label: 'USD' },
  { value: 'EUR', label: 'EUR' },
  { value: 'GBP', label: 'GBP' },
  { value: 'CAD', label: 'CAD' },
  { value: 'AUD', label: 'AUD' },
]

const TRAVEL_OPTIONS: { value: TravelRequired; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'occasional', label: 'Occasional' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'extensive', label: 'Extensive' },
]

const START_DATE_OPTIONS: { value: StartDatePref; label: string }[] = [
  { value: 'immediate', label: 'Immediate' },
  { value: 'within_30_days', label: 'Within 30 days' },
  { value: 'within_60_days', label: 'Within 60 days' },
  { value: 'flexible', label: 'Flexible' },
]

const createJobSchema = z.object({
  org_unit_id: z.string().uuid('Select an org unit'),
  title: z.string().min(1, 'Title is required').max(300),
  target_headcount: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().int().min(1).max(10_000).nullable().optional(),
  ),
  employment_type: z
    .enum(['full_time', 'part_time', 'contract', 'contract_to_hire', 'internship'])
    .nullable()
    .optional(),
  work_arrangement: z.enum(['onsite', 'remote', 'hybrid']).nullable().optional(),
  location: z.string().max(500).optional().nullable(),
  salary_range_min: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional(),
  ),
  salary_range_max: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional(),
  ),
  salary_currency: z.enum(['USD', 'EUR', 'GBP', 'INR', 'CAD', 'AUD']).nullable().optional(),
  travel_required: z.enum(['none', 'occasional', 'moderate', 'extensive']).nullable().optional(),
  start_date_pref: z
    .enum(['immediate', 'within_30_days', 'within_60_days', 'flexible'])
    .nullable()
    .optional(),
})

type CreateJobForm = z.infer<typeof createJobSchema>

type OrgUnit = {
  id: string
  name: string
  unit_type: string
  parent_unit_id: string | null
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <Label className="px-label">{label}</Label>
      {children}
      {error ? (
        <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
          {error}
        </p>
      ) : hint ? (
        <p className="px-hint">{hint}</p>
      ) : null}
    </div>
  )
}

export default function NewJobPage() {
  const router = useRouter()

  const {
    data: units,
    isLoading: unitsLoading,
    error: unitsError,
  } = useQuery<OrgUnit[]>({
    queryKey: ['org-units'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit[]>('/api/org-units', { token, signal })
    },
  })

  const form = useForm<CreateJobForm>({
    resolver: zodResolver(createJobSchema) as Resolver<CreateJobForm>,
    defaultValues: {
      org_unit_id: '',
      title: '',
      target_headcount: null,
      employment_type: null,
      work_arrangement: null,
      location: '',
      salary_range_min: null,
      salary_range_max: null,
      salary_currency: null,
      travel_required: null,
      start_date_pref: null,
    },
    mode: 'onChange',
  })

  const createMutation = useMutation({
    mutationFn: async (data: CreateJobForm) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.create(token, {
        org_unit_id: data.org_unit_id,
        title: data.title,
        target_headcount: data.target_headcount ?? null,
        deadline: null,
        employment_type: data.employment_type ?? null,
        work_arrangement: data.work_arrangement ?? null,
        location: data.location || null,
        salary_range_min: data.salary_range_min ?? null,
        salary_range_max: data.salary_range_max ?? null,
        salary_currency: data.salary_currency ?? null,
        travel_required: data.travel_required ?? null,
        start_date_pref: data.start_date_pref ?? null,
      })
    },
    onSuccess: (job) => {
      toast.success('Role created — add the JD next')
      router.push(`/jobs/${job.id}`)
    },
    onError: (err: Error) => {
      toast.error(`Create failed: ${err.message}`)
    },
  })

  const eligibleUnits = units ?? []

  if (unitsLoading) {
    return (
      <div
        className="mx-auto max-w-[820px] px-8 pt-7 text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Loading org units…
      </div>
    )
  }

  if (unitsError) {
    return (
      <div
        className="mx-auto max-w-[820px] px-8 pt-7 text-sm"
        style={{ color: 'var(--px-danger)' }}
      >
        Failed to load org units: {(unitsError as Error).message}
      </div>
    )
  }

  if (eligibleUnits.length === 0) {
    return (
      <div className="mx-auto max-w-[820px] px-8 pt-7">
        <div
          className="rounded-[14px] border p-8"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <h1
            className="px-serif m-0 mb-3 text-2xl"
            style={{ color: 'var(--px-fg)' }}
          >
            No org units available
          </h1>
          <p className="mb-5 text-sm" style={{ color: 'var(--px-fg-3)' }}>
            You need access to at least one org unit before creating a role.
            Visit Settings → Org units to set one up.
          </p>
          <Link href="/settings/org-units">
            <Button size="sm">Go to Org units</Button>
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto px-8 pb-14 pt-7" style={{ maxWidth: 820 }}>
      <div className="mb-7">
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
        >
          New role
        </h1>
        <div
          className="mt-1 text-[13px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          Set the basics. You&apos;ll paste the job description on the next
          screen, then enrich it and extract signals.
        </div>
      </div>

      <form
        onSubmit={form.handleSubmit(
          (data) => createMutation.mutate(data),
          (errors) => {
            const first = Object.values(errors)[0]
            if (first?.message) toast.error(`Validation: ${first.message}`)
          },
        )}
      >
        <div className="space-y-[22px]">
          <Field
            label="Role title"
            hint="The title candidates will see."
            error={form.formState.errors.title?.message}
          >
            <Input
              {...form.register('title')}
              placeholder="e.g. Staff Backend Engineer"
            />
          </Field>

          <Field label="Org unit" error={form.formState.errors.org_unit_id?.message}>
            <select
              className="px-input"
              value={form.watch('org_unit_id')}
              onChange={(e) =>
                form.setValue('org_unit_id', e.target.value, { shouldValidate: true })
              }
            >
              <option value="" disabled>
                Select org unit
              </option>
              {eligibleUnits.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.unit_type})
                </option>
              ))}
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Employment type">
              <select
                className="px-input"
                value={form.watch('employment_type') ?? ''}
                onChange={(e) =>
                  form.setValue(
                    'employment_type',
                    (e.target.value || null) as EmploymentType | null,
                  )
                }
              >
                <option value="">Select type</option>
                {EMPLOYMENT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Work arrangement">
              <select
                className="px-input"
                value={form.watch('work_arrangement') ?? ''}
                onChange={(e) =>
                  form.setValue(
                    'work_arrangement',
                    (e.target.value || null) as WorkArrangement | null,
                  )
                }
              >
                <option value="">Select arrangement</option>
                {WORK_ARRANGEMENT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {(form.watch('work_arrangement') === 'onsite' ||
            form.watch('work_arrangement') === 'hybrid') && (
            <Field label="Location" hint="Primary location or city.">
              <Input {...form.register('location')} placeholder="e.g. San Francisco · hybrid" />
            </Field>
          )}

          <Field
            label="Salary range"
            hint="Internal — candidates do not see this."
          >
            <div className="grid grid-cols-3 gap-3">
              <Input type="number" placeholder="Min" {...form.register('salary_range_min')} />
              <Input type="number" placeholder="Max" {...form.register('salary_range_max')} />
              <select
                className="px-input"
                value={form.watch('salary_currency') ?? ''}
                onChange={(e) =>
                  form.setValue(
                    'salary_currency',
                    (e.target.value || null) as SalaryCurrency | null,
                  )
                }
              >
                <option value="">Currency</option>
                {SALARY_CURRENCY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Travel required">
              <select
                className="px-input"
                value={form.watch('travel_required') ?? ''}
                onChange={(e) =>
                  form.setValue(
                    'travel_required',
                    (e.target.value || null) as TravelRequired | null,
                  )
                }
              >
                <option value="">Select</option>
                {TRAVEL_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Preferred start date">
              <select
                className="px-input"
                value={form.watch('start_date_pref') ?? ''}
                onChange={(e) =>
                  form.setValue(
                    'start_date_pref',
                    (e.target.value || null) as StartDatePref | null,
                  )
                }
              >
                <option value="">Select</option>
                {START_DATE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field
            label="Target headcount"
            hint="Optional — how many hires are you planning for this role?"
          >
            <Input
              type="number"
              min={1}
              placeholder="e.g. 3"
              {...form.register('target_headcount')}
            />
          </Field>
        </div>

        <div
          className="mt-7 flex items-center gap-2.5 border-t pt-5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <Link href="/jobs">
            <button type="button" className="px-btn ghost sm">
              Cancel
            </button>
          </Link>
          <div className="flex-1" />
          <button
            type="submit"
            className="px-btn primary sm"
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? 'Creating…' : 'Create role'}
          </button>
        </div>
      </form>
    </div>
  )
}
