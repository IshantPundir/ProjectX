'use client'

import { useQuery } from '@tanstack/react-query'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/px'
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
        {/*
          Native <select><option> can only contain plain text, so we flatten
          the previous two-line rich layout to "Title · org unit". The
          px Select primitive walks children looking for SelectItem nodes and
          renders their `children` prop as the option label — keeping it a
          string keeps hydration valid.
        */}
        <SelectItem value={ALL_JDS_SENTINEL}>
          All JDs · show candidates across every role
        </SelectItem>
        {(jobs ?? []).map((job) => (
          <SelectItem key={job.id} value={job.id}>
            {job.org_unit_name
              ? `${job.title} · ${job.org_unit_name}`
              : job.title}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
