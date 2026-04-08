'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
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
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const createJobSchema = z.object({
  org_unit_id: z.string().uuid('Select an org unit'),
  title: z.string().min(1, 'Title is required').max(300),
  description_raw: z
    .string()
    .min(50, 'JD must be at least 50 characters')
    .max(50_000),
  project_scope_raw: z.string().max(20_000).optional().nullable(),
  target_headcount: z
    .number()
    .int()
    .min(1)
    .max(10_000)
    .optional()
    .nullable(),
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
  const { data: units, isLoading: unitsLoading } = useQuery<OrgUnit[]>({
    queryKey: ['org-units'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit[]>('/api/org-units', { token })
    },
  })

  const form = useForm<CreateJobForm>({
    resolver: zodResolver(createJobSchema),
    defaultValues: {
      org_unit_id: '',
      title: '',
      description_raw: '',
      project_scope_raw: '',
      target_headcount: null,
    },
    mode: 'onChange',
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
    return <div className="text-sm text-zinc-500">Loading org units…</div>
  }

  if (eligibleUnits.length === 0) {
    return (
      <div className="max-w-xl bg-white border border-zinc-200 rounded-lg p-8">
        <h1 className="text-xl font-semibold text-zinc-900 mb-3">
          No org units available
        </h1>
        <p className="text-sm text-zinc-600 mb-5">
          You need access to at least one org unit with a completed company
          profile before creating a job description. Visit Settings → Org Units
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
        onSubmit={form.handleSubmit((data) => createMutation.mutate(data))}
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

        <Button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? 'Creating…' : 'Create and enhance'}
        </Button>
      </form>
    </div>
  )
}
