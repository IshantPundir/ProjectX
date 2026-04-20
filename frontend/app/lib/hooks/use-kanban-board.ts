'use client'

import { useQuery } from '@tanstack/react-query'

import {
  candidatesApi,
  type KanbanBoardResponse,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useKanbanBoard(jobId: string | null) {
  return useQuery<KanbanBoardResponse>({
    queryKey: ['candidates-kanban', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.kanban(token, jobId as string)
    },
    enabled: !!jobId,
  })
}
