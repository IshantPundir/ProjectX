'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  questionBanksApi,
  type GenerateResponse,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useGenerateStageQuestions(jobId: string, stageId: string) {
  const queryClient = useQueryClient()

  return useMutation<GenerateResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.generateStage(token, jobId, stageId)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to start generation: ${error.message}`)
    },
  })
}

export function useGenerateAllQuestions(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<GenerateResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.generateAll(token, jobId)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
    },
    onError: (error) => {
      toast.error(`Failed to start generation: ${error.message}`)
    },
  })
}
