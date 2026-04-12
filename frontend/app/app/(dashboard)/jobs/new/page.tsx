'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm, useWatch, type Resolver } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
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
  { value: 'full_time', label: 'Full Time' },
  { value: 'part_time', label: 'Part Time' },
  { value: 'contract', label: 'Contract' },
  { value: 'contract_to_hire', label: 'Contract to Hire' },
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
  description_raw: z
    .string()
    .min(50, 'JD must be at least 50 characters')
    .max(50_000),
  project_scope_raw: z.string().max(20_000).optional().nullable(),
  target_headcount: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().int().min(1).max(10_000).nullable().optional(),
  ),
  // Metadata fields — all optional
  employment_type: z
    .enum(['full_time', 'part_time', 'contract', 'contract_to_hire', 'internship'])
    .nullable()
    .optional(),
  work_arrangement: z
    .enum(['onsite', 'remote', 'hybrid'])
    .nullable()
    .optional(),
  location: z.string().max(500).optional().nullable(),
  salary_range_min: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional(),
  ),
  salary_range_max: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional(),
  ),
  salary_currency: z
    .enum(['USD', 'EUR', 'GBP', 'INR', 'CAD', 'AUD'])
    .nullable()
    .optional(),
  travel_required: z
    .enum(['none', 'occasional', 'moderate', 'extensive'])
    .nullable()
    .optional(),
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

export default function NewJobPage() {
  const router = useRouter()

  // Fetch all org units the user can see. Backend filters by permission;
  // the 422 CompanyProfileIncompleteError handler catches units without
  // a completed profile in ancestry.
  const { data: units, isLoading: unitsLoading, error: unitsError } = useQuery<OrgUnit[]>({
    queryKey: ['org-units'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit[]>('/api/org-units', { token })
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
    },
    mode: 'onChange',
  })

  const workArrangement = useWatch({ control: form.control, name: 'work_arrangement' })

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
      })
    },
    onSuccess: (job) => {
      toast.success('Job created — running extraction')
      router.push(`/jobs/${job.id}`)
    },
    onError: (err: Error) => {
      toast.error(`Create failed: ${err.message}`)
    },
  })

  const eligibleUnits = units || []

  if (unitsLoading) {
    return <div className="text-sm text-zinc-500">Loading org units...</div>
  }

  if (unitsError) {
    return (
      <div className="text-sm text-red-500">
        Failed to load org units: {(unitsError as Error).message}
      </div>
    )
  }

  if (eligibleUnits.length === 0) {
    return (
      <div className="max-w-xl bg-white border border-zinc-200 rounded-lg p-8">
        <h1 className="text-xl font-semibold text-zinc-900 mb-3">
          No org units available
        </h1>
        <p className="text-sm text-zinc-600 mb-5">
          You need access to at least one org unit with a completed company
          profile before creating a job description. Visit Settings &rarr; Org Units
          to set up your company profile.
        </p>
        <Link href="/settings/org-units">
          <Button>Go to Org Units</Button>
        </Link>
      </div>
    )
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-zinc-900 mb-6">
        New Job Description
      </h1>
      <form
        onSubmit={form.handleSubmit(
          (data) => createMutation.mutate(data),
          (errors) => {
            const firstError = Object.values(errors)[0]
            if (firstError?.message) {
              toast.error(`Validation: ${firstError.message}`)
            }
          },
        )}
        className="space-y-6"
      >
        <div>
          <Label htmlFor="org_unit_id">Org Unit</Label>
          {/* Base UI Select.Root uses onValueChange((value, eventDetails) => void).
              value is typed as unknown — cast to string; UUID format validated by Zod. */}
          <Select
            onValueChange={(v) =>
              form.setValue('org_unit_id', v as string, { shouldValidate: true })
            }
          >
            <SelectTrigger id="org_unit_id" className="mt-2 w-full">
              <SelectValue placeholder="Select org unit" />
            </SelectTrigger>
            <SelectContent>
              {eligibleUnits.map((u) => (
                <SelectItem key={u.id} value={u.id}>
                  {u.name} ({u.unit_type})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.org_unit_id && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.org_unit_id.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="title">Title</Label>
          <Input id="title" {...form.register('title')} className="mt-2" />
          {form.formState.errors.title && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.title.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="description_raw">Job Description</Label>
          <p className="text-xs text-zinc-500 mt-1 mb-2">
            Paste the full raw JD. The AI will enrich it and extract
            structured signals.
          </p>
          <Textarea
            id="description_raw"
            {...form.register('description_raw')}
            rows={14}
            className="font-mono text-sm"
          />
          {form.formState.errors.description_raw && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.description_raw.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="project_scope_raw">Project Scope (optional)</Label>
          <p className="text-xs text-zinc-500 mt-1 mb-2">
            What will this hire build in their first 90 days? Significantly
            improves question specificity.
          </p>
          <Textarea
            id="project_scope_raw"
            {...form.register('project_scope_raw')}
            rows={5}
          />
        </div>

        {/* --- Additional Details --- */}
        <div className="border-t border-zinc-200 pt-6">
          <h2 className="text-sm font-medium text-zinc-500 mb-4">
            Additional Details
          </h2>

          <div className="grid grid-cols-2 gap-4">
            {/* Employment Type */}
            <div>
              <Label htmlFor="employment_type">Employment Type</Label>
              <Select
                onValueChange={(v) =>
                  form.setValue('employment_type', (v || null) as EmploymentType | null, {
                    shouldValidate: true,
                  })
                }
              >
                <SelectTrigger id="employment_type" className="mt-2 w-full">
                  <SelectValue placeholder="Select type" />
                </SelectTrigger>
                <SelectContent>
                  {EMPLOYMENT_TYPE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Work Arrangement */}
            <div>
              <Label htmlFor="work_arrangement">Work Arrangement</Label>
              <Select
                onValueChange={(v) =>
                  form.setValue('work_arrangement', (v || null) as WorkArrangement | null, {
                    shouldValidate: true,
                  })
                }
              >
                <SelectTrigger id="work_arrangement" className="mt-2 w-full">
                  <SelectValue placeholder="Select arrangement" />
                </SelectTrigger>
                <SelectContent>
                  {WORK_ARRANGEMENT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Location — shown when hybrid or onsite */}
          {(workArrangement === 'onsite' || workArrangement === 'hybrid') && (
            <div className="mt-4">
              <Label htmlFor="location">Location</Label>
              <Input
                id="location"
                {...form.register('location')}
                placeholder="e.g. San Francisco, CA"
                className="mt-2"
              />
            </div>
          )}

          {/* Salary Range */}
          <div className="mt-4">
            <Label>Salary Range (Annual, for internal screening only)</Label>
            <div className="grid grid-cols-3 gap-3 mt-2">
              <div>
                <Input
                  type="number"
                  placeholder="Min (annual)"
                  {...form.register('salary_range_min')}
                />
              </div>
              <div>
                <Input
                  type="number"
                  placeholder="Max (annual)"
                  {...form.register('salary_range_max')}
                />
              </div>
              <div>
                <Select
                  onValueChange={(v) =>
                    form.setValue('salary_currency', (v || null) as SalaryCurrency | null, {
                      shouldValidate: true,
                    })
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Currency" />
                  </SelectTrigger>
                  <SelectContent>
                    {SALARY_CURRENCY_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 mt-4">
            {/* Travel Required */}
            <div>
              <Label htmlFor="travel_required">Travel Required</Label>
              <Select
                onValueChange={(v) =>
                  form.setValue('travel_required', (v || null) as TravelRequired | null, {
                    shouldValidate: true,
                  })
                }
              >
                <SelectTrigger id="travel_required" className="mt-2 w-full">
                  <SelectValue placeholder="Select" />
                </SelectTrigger>
                <SelectContent>
                  {TRAVEL_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Start Date Preference */}
            <div>
              <Label htmlFor="start_date_pref">Preferred Start Date</Label>
              <Select
                onValueChange={(v) =>
                  form.setValue('start_date_pref', (v || null) as StartDatePref | null, {
                    shouldValidate: true,
                  })
                }
              >
                <SelectTrigger id="start_date_pref" className="mt-2 w-full">
                  <SelectValue placeholder="Select" />
                </SelectTrigger>
                <SelectContent>
                  {START_DATE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <Button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? 'Creating...' : 'Create and enhance'}
        </Button>
      </form>
    </div>
  )
}
