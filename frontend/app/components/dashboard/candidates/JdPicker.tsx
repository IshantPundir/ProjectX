'use client'

import { useQuery } from '@tanstack/react-query'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

// Sentinel value for the "all" option. Base UI's Select treats `null`/`undefined`
// as "no selection" (shows the placeholder), so we model the "All JDs" option
// with an explicit sentinel string and translate to/from `null` at the props
// boundary.
const ALL_JDS_SENTINEL = '__all__'

interface Props {
  value: string | null
  onChange: (id: string | null) => void
}

export function JdPicker({ value, onChange }: Props) {
  const { data: jobs, isLoading } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

  const selectValue = value ?? ALL_JDS_SENTINEL

  // Base UI's `onValueChange` types the value as `unknown` — narrow before use.
  function handleChange(next: unknown) {
    const str = typeof next === 'string' ? next : ''
    if (!str || str === ALL_JDS_SENTINEL) {
      onChange(null)
    } else {
      onChange(str)
    }
  }

  return (
    <Select value={selectValue} onValueChange={handleChange}>
      <SelectTrigger
        id="candidates-jd-picker"
        className="w-72"
        aria-label="Select a job description"
      >
        <SelectValue placeholder={isLoading ? 'Loading jobs…' : 'Select a JD…'} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_JDS_SENTINEL}>
          <span className="flex flex-col">
            <span className="text-sm">All JDs (cross-JD view)</span>
            <span className="text-[11px] text-zinc-500">
              Show candidates across every job
            </span>
          </span>
        </SelectItem>
        {(jobs ?? []).map((job) => (
          <SelectItem key={job.id} value={job.id}>
            <span className="flex flex-col">
              <span className="text-sm">{job.title}</span>
              {job.org_unit_name && (
                <span className="text-[11px] text-zinc-500">
                  {job.org_unit_name}
                </span>
              )}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
