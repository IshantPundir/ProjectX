'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { pipelinesApi, type JobPipelineInstance } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Persists a stage's OTP-required default (job_pipeline_stages.otp_required_default).
 * On success the returned instance is written straight into the job-pipeline
 * cache so the column toggle reflects the authoritative value without a refetch.
 */
export function useSetStageOtp(jobId: string) {
  const qc = useQueryClient()
  return useMutation<
    JobPipelineInstance,
    Error,
    { stageId: string; otpRequired: boolean }
  >({
    mutationFn: async ({ stageId, otpRequired }) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.setStageOtpRequired(token, jobId, stageId, otpRequired)
    },
    onSuccess: (instance) => {
      qc.setQueryData(['job-pipeline', jobId], instance)
    },
  })
}
