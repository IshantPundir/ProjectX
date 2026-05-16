'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/lib/api/client'
import { pipelinesApi } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useActivateJob(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.activate(token, jobId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs', jobId] })
      qc.invalidateQueries({ queryKey: ['jobs-list'] })
      toast.success('Job activated — ready to onboard candidates.')
    },
    onError: (err) => {
      // 422 means the server's activation predicates failed. The banner
      // already renders the bulleted failure list above the button; the
      // toast just confirms the click was acknowledged so the user
      // doesn't think nothing happened (the prior bug).
      if (err instanceof ApiError && err.status === 422) {
        toast.error('Fix the items above before activating.')
        return
      }
      // 409 means the job isn't in pipeline_built state. Two practical
      // causes: (a) it was already activated by someone else (refresh
      // resolves it), or (b) it's still in signals_confirmed because
      // the auto-pipeline migration hasn't run on its row (a one-off
      // for jobs confirmed before the auto-build hook landed).
      if (err instanceof ApiError && err.status === 409) {
        toast.error(
          "This job isn't ready to activate. Refresh the page — if the issue persists, the pipeline never auto-built for this row.",
        )
        return
      }
      toast.error(
        err instanceof Error
          ? `Activation failed: ${err.message}`
          : 'Activation failed. Please try again.',
      )
    },
  })
}
