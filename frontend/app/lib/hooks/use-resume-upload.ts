'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { candidatesApi } from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

// Orchestrates resume upload: pre-sign URL -> PUT to S3 -> confirm with backend.
// The S3 PUT is direct (bypasses Nexus) and must match the pre-signed headers.
export function useResumeUpload(candidateId: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, File>({
    mutationFn: async (file) => {
      const token = await getFreshSupabaseToken()
      const pre = await candidatesApi.requestResumeUpload(token, candidateId)
      const putResp = await fetch(pre.upload_url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/pdf' },
        body: file,
      })
      if (!putResp.ok) {
        throw new Error(`S3 upload failed (${putResp.status})`)
      }
      await candidatesApi.confirmResumeUpload(token, candidateId, pre.s3_key)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId],
      })
    },
  })
}
