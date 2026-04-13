'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  questionBanksApi,
  type BankResponse,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useConfirmBank(jobId: string, stageId: string) {
  const queryClient = useQueryClient()

  return useMutation<BankResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.confirmBank(token, jobId, stageId)
    },
    onSuccess: () => {
      toast.success('Bank confirmed. Ready for interview sessions.')
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to confirm: ${error.message}`)
    },
  })
}
