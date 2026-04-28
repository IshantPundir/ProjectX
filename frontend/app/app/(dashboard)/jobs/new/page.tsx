'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useMemo, useState } from 'react'
import { useForm, useWatch, type Resolver } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button, Input, Label, Textarea } from '@/components/px'
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
  description_raw: z.string().min(50, 'JD must be at least 50 characters').max(50_000),
  project_scope_raw: z.string().max(20_000).optional().nullable(),
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
  skip_enrichment: z.boolean().default(false),
})

type CreateJobForm = z.infer<typeof createJobSchema>

type OrgUnit = {
  id: string
  name: string
  unit_type: string
  parent_unit_id: string | null
}

/* ─── Wizard progress strip — matches v4 design (filled bars + padded 0N prefix) ─── */

const STEPS = [
  { n: 1, label: 'Basics' },
  { n: 2, label: 'Job description' },
  { n: 3, label: 'Review & publish' },
] as const

function WizardProgress({
  current,
  onPick,
}: {
  current: 1 | 2 | 3
  onPick: (n: 1 | 2 | 3) => void
}) {
  return (
    <div className="mb-7 flex gap-0.5">
      {STEPS.map((s) => {
        const filled = current >= s.n
        const active = current === s.n
        return (
          <div
            key={s.n}
            className="flex-1 cursor-pointer"
            onClick={() => onPick(s.n as 1 | 2 | 3)}
          >
            <div
              className="mb-2 h-[3px] rounded-sm"
              style={{
                background: filled ? 'var(--px-accent)' : 'var(--px-surface-3)',
              }}
            />
            <div className="flex items-center gap-1.5">
              <span
                className="px-mono text-[10px] font-semibold"
                style={{
                  color: filled ? 'var(--px-accent)' : 'var(--px-fg-4)',
                }}
              >
                0{s.n}
              </span>
              <span
                className="text-[12px]"
                style={{
                  color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {s.label}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ─── Field wrapper ──────────────────────────────────────── */

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

/* ─── Page ───────────────────────────────────────────────── */

export default function NewJobPage() {
  const router = useRouter()
  const [step, setStep] = useState<1 | 2 | 3>(1)

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
      description_raw: '',
      project_scope_raw: '',
      target_headcount: null,
      employment_type: null,
      work_arrangement: null,
      location: '',
      salary_range_min: null,
      salary_range_max: null,
      salary_currency: null,
      travel_required: null,
      start_date_pref: null,
      skip_enrichment: false,
    },
    mode: 'onChange',
  })

  const [
    watchedOrgUnitId,
    watchedTitle,
    watchedEmploymentType,
    watchedWorkArrangement,
    watchedLocation,
    watchedSalaryMin,
    watchedSalaryMax,
    watchedSalaryCurrency,
    watchedTargetHeadcount,
    watchedDescriptionRaw,
    watchedProjectScopeRaw,
  ] = useWatch({
    control: form.control,
    name: [
      'org_unit_id',
      'title',
      'employment_type',
      'work_arrangement',
      'location',
      'salary_range_min',
      'salary_range_max',
      'salary_currency',
      'target_headcount',
      'description_raw',
      'project_scope_raw',
    ] as const,
  })

  const createMutation = useMutation({
    mutationFn: async (data: CreateJobForm) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.create(token, {
        org_unit_id: data.org_unit_id,
        title: data.title,
        description_raw: data.description_raw,
        project_scope_raw: data.project_scope_raw || null,
        target_headcount: data.target_headcount || null,
        deadline: null,
        employment_type: data.employment_type || null,
        work_arrangement: data.work_arrangement || null,
        location: data.location || null,
        salary_range_min: data.salary_range_min || null,
        salary_range_max: data.salary_range_max || null,
        salary_currency: data.salary_currency || null,
        travel_required: data.travel_required || null,
        start_date_pref: data.start_date_pref || null,
        skip_enrichment: data.skip_enrichment,
      })
    },
    onSuccess: (job) => {
      toast.success('Role created — running signal extraction')
      router.push(`/jobs/${job.id}`)
    },
    onError: (err: Error) => {
      toast.error(`Create failed: ${err.message}`)
    },
  })

  const eligibleUnits = units ?? []
  const unitById = useMemo(
    () => new Map(eligibleUnits.map((u) => [u.id, u])),
    [eligibleUnits],
  )

  async function goNext() {
    let ok = true
    if (step === 1) {
      ok = await form.trigger(['org_unit_id', 'title'])
    } else if (step === 2) {
      ok = await form.trigger(['description_raw'])
    }
    if (!ok) {
      const errs = form.formState.errors
      const first = Object.values(errs)[0]
      if (first?.message) toast.error(`${first.message}`)
      return
    }
    setStep((s) => (s === 3 ? s : ((s + 1) as 1 | 2 | 3)))
  }

  function goBack() {
    setStep((s) => (s === 1 ? s : ((s - 1) as 1 | 2 | 3)))
  }

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
            You need access to at least one org unit with a completed company
            profile before creating a role. Visit Settings → Org units to set up
            your company profile.
          </p>
          <Link href="/settings/org-units">
            <Button size="sm">Go to Org units</Button>
          </Link>
        </div>
      </div>
    )
  }

  const maxWidth = step === 2 ? 1180 : 820

  return (
    <div
      className="mx-auto px-8 pb-14 pt-7"
      style={{ maxWidth }}
    >
      {/* Wizard header */}
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
          Draft it, let Copilot extract signals, publish when it&apos;s right.
        </div>
      </div>

      <WizardProgress current={step} onPick={setStep} />

      <form
        onSubmit={form.handleSubmit(
          (data) => createMutation.mutate(data),
          (errors) => {
            const first = Object.values(errors)[0]
            if (first?.message) toast.error(`Validation: ${first.message}`)
          },
        )}
      >
        {/* Step 1 — Basics */}
        {step === 1 && (
          <div className="space-y-[22px]">
            <Field label="Role title" hint="The title candidates will see." error={form.formState.errors.title?.message}>
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
        )}

        {/* Step 2 — JD + scope — presented as a review surface */}
        {step === 2 && (
          <div
            className="rounded-[14px] border p-7"
            style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
          >
            <div className="mb-6 max-w-[720px]">
              <h2
                className="px-serif m-0 mb-3 text-[24px] font-normal"
                style={{ letterSpacing: '-0.3px', color: 'var(--px-fg)' }}
              >
                Paste the job description, or describe the scope.
              </h2>
              <p className="text-[13.5px]" style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}>
                Copilot will pull out must-haves, nice-to-haves, and interview
                questions after you publish. You&apos;ll see exactly where each
                one came from, and you can adjust anything before it ships.
              </p>
            </div>

            <div className="space-y-[22px]">
              <Field
                label="Job description"
                error={form.formState.errors.description_raw?.message}
              >
                <Textarea
                  {...form.register('description_raw')}
                  rows={14}
                  className="font-mono text-[13px]"
                  placeholder="Paste the job description here…"
                />
              </Field>

              <Field
                label="Project scope"
                hint="What will this hire build in their first 90 days? Significantly improves question specificity."
              >
                <Textarea {...form.register('project_scope_raw')} rows={5} />
              </Field>
            </div>

            <div
              className="flex items-start gap-3 rounded-md border p-3.5 mt-2"
              style={{
                background: 'var(--px-surface-2)',
                borderColor: 'var(--px-hairline)',
              }}
            >
              <input
                type="checkbox"
                id="enrich-toggle"
                className="mt-0.5"
                checked={!form.watch('skip_enrichment')}
                onChange={(e) =>
                  form.setValue('skip_enrichment', !e.target.checked, {
                    shouldDirty: true,
                  })
                }
              />
              <label htmlFor="enrich-toggle" className="flex-1 text-[13px]" style={{ color: 'var(--px-fg-2)' }}>
                <div style={{ color: 'var(--px-fg)', fontWeight: 600 }}>
                  Enrich JD with Copilot
                </div>
                <div className="mt-0.5 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
                  Off if your JD is already polished — Copilot will extract signals from it as-is.
                </div>
              </label>
            </div>
          </div>
        )}

        {/* Step 3 — Review */}
        {step === 3 && (
          <div className="space-y-[22px]">
            <div
              className="rounded-[14px] border p-6"
              style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
            >
              <h3
                className="px-eyebrow mb-4"
                style={{ margin: 0, marginBottom: 14 }}
              >
                Summary
              </h3>
              <div className="space-y-3">
                <Summary label="Org unit" value={unitById.get(watchedOrgUnitId ?? '')?.name ?? '—'} />
                <Summary label="Role title" value={watchedTitle || '—'} />
                <Summary
                  label="Employment"
                  value={
                    [
                      EMPLOYMENT_TYPE_OPTIONS.find((o) => o.value === watchedEmploymentType)?.label,
                      WORK_ARRANGEMENT_OPTIONS.find((o) => o.value === watchedWorkArrangement)?.label,
                      watchedLocation,
                    ]
                      .filter(Boolean)
                      .join(' · ') || '—'
                  }
                />
                <Summary
                  label="Compensation"
                  value={
                    watchedSalaryMin && watchedSalaryMax
                      ? `${watchedSalaryCurrency ?? ''} ${watchedSalaryMin.toLocaleString()}–${watchedSalaryMax.toLocaleString()}`
                      : '—'
                  }
                />
                <Summary
                  label="Target headcount"
                  value={watchedTargetHeadcount ? String(watchedTargetHeadcount) : '—'}
                />
                <Summary
                  label="JD length"
                  value={`${(watchedDescriptionRaw ?? '').length.toLocaleString()} characters`}
                />
                <Summary
                  label="Project scope"
                  value={
                    watchedProjectScopeRaw
                      ? `${watchedProjectScopeRaw.length.toLocaleString()} characters`
                      : 'not provided'
                  }
                />
              </div>
            </div>

            <div
              className="flex items-start gap-2.5 rounded-md border p-3.5 text-[12.5px]"
              style={{
                background: 'var(--px-accent-tint)',
                borderColor: 'var(--px-accent-line)',
                color: 'var(--px-accent-2)',
              }}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
              </svg>
              <div>
                <b>Next:</b> After you publish, Copilot will extract signals in
                the background. You&apos;ll land on the review page where you
                can confirm or edit them.
              </div>
            </div>
          </div>
        )}

        {/* Footer actions */}
        <div
          className="mt-7 flex items-center gap-2.5 border-t pt-5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          {step > 1 ? (
            <button type="button" className="px-btn outline sm" onClick={goBack}>
              ← Back
            </button>
          ) : (
            <Link href="/jobs">
              <button type="button" className="px-btn ghost sm">
                Cancel
              </button>
            </Link>
          )}
          <div className="flex-1" />
          {step === 1 && (
            <button type="button" className="px-btn primary sm" onClick={goNext}>
              Next: Job description →
            </button>
          )}
          {step === 2 && (
            <>
              <button type="button" className="px-btn ghost sm" onClick={goNext}>
                Skip for now →
              </button>
              <button type="button" className="px-btn primary sm" onClick={goNext}>
                Next: Review →
              </button>
            </>
          )}
          {step === 3 && (
            <>
              <span className="px-copilot-strip">
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.8}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
                </svg>
                Copilot extracts signals after save
              </span>
              <button
                type="submit"
                className="px-btn primary sm"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? 'Creating…' : 'Publish role'}
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  )
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="grid items-center gap-4"
      style={{ gridTemplateColumns: '150px 1fr' }}
    >
      <div
        className="px-eyebrow"
        style={{ margin: 0 }}
      >
        {label}
      </div>
      <div className="text-[13.5px]" style={{ color: 'var(--px-fg)' }}>
        {value}
      </div>
    </div>
  )
}
