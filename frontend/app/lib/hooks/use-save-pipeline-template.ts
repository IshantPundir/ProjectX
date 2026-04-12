'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type CreateTemplateBody,
  type PipelineTemplate,
  type UpdateTemplateBody,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateTemplate(unitId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, CreateTemplateBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.createTemplate(token, unitId, body)
    },
    onSuccess: () => {
      toast.success('Template created')
      void qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to create template: ${err.message}`),
  })
}

export function useUpdateTemplate(unitId: string, templateId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, UpdateTemplateBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.updateTemplate(token, templateId, body)
    },
    onSuccess: () => {
      toast.success('Template saved')
      void qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to save: ${err.message}`),
  })
}

export function useSetDefault(unitId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, string>({
    mutationFn: async (templateId) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.setDefault(token, templateId)
    },
    onSuccess: () => {
      toast.success('Default template updated')
      void qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to set default: ${err.message}`),
  })
}

export function useDeleteTemplate(unitId: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (templateId) => {
      const token = await getFreshSupabaseToken()
      await pipelinesApi.deleteTemplate(token, templateId)
    },
    onSuccess: () => {
      toast.success('Template deleted')
      void qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to delete: ${err.message}`),
  })
}
