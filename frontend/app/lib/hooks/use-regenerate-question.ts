'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  questionBanksApi,
  type GenerateResponse,
  type RegenerateQuestionBody,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useRegenerateQuestion(
  jobId: string,
  stageId: string,
  questionId: string,
) {
  const queryClient = useQueryClient()

  return useMutation<GenerateResponse, Error, RegenerateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.regenerateQuestion(
        token,
        jobId,
        stageId,
        questionId,
        body,
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to regenerate: ${error.message}`)
    },
  })
}
