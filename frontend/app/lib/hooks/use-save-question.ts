'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  questionBanksApi,
  type BankWithQuestionsResponse,
  type CreateQuestionBody,
  type QuestionResponse,
  type ReorderBody,
  type UpdateQuestionBody,
} from '@/lib/api/question-banks'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateQuestion(jobId: string, stageId: string) {
  const queryClient = useQueryClient()

  return useMutation<QuestionResponse, Error, CreateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.createQuestion(token, jobId, stageId, body)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to create question: ${error.message}`)
    },
  })
}

export function useUpdateQuestion(
  jobId: string,
  stageId: string,
  questionId: string,
) {
  const queryClient = useQueryClient()

  return useMutation<QuestionResponse, Error, UpdateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.updateQuestion(
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
      toast.error(`Failed to save question: ${error.message}`)
    },
  })
}

export function useDeleteQuestion(jobId: string, stageId: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, string>({
    mutationFn: async (questionId) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.deleteQuestion(token, jobId, stageId, questionId)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to delete question: ${error.message}`)
    },
  })
}

export function useReorderQuestions(jobId: string, stageId: string) {
  const queryClient = useQueryClient()

  return useMutation<BankWithQuestionsResponse, Error, ReorderBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.reorderQuestions(token, jobId, stageId, body)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
      void queryClient.invalidateQueries({
        queryKey: ['bank', jobId, stageId],
      })
    },
    onError: (error) => {
      toast.error(`Failed to reorder: ${error.message}`)
    },
  })
}
