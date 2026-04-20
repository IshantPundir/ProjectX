'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  candidatesApi,
  type AssignmentResponse,
  type KanbanBoardResponse,
  type KanbanCandidateCard,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

interface TransitionCandidateVars {
  candidateId: string
  assignmentId: string
  targetStageId: string
  reason?: string
  override?: boolean
}

interface TransitionCandidateContext {
  snapshot?: KanbanBoardResponse
}

export function useTransitionCandidate(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    AssignmentResponse,
    Error,
    TransitionCandidateVars,
    TransitionCandidateContext
  >({
    mutationFn: async ({
      candidateId,
      assignmentId,
      targetStageId,
      reason,
      override,
    }) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.transitionStage(token, candidateId, assignmentId, {
        target_stage_id: targetStageId,
        reason,
        override,
      })
    },
    onMutate: async (vars) => {
      await queryClient.cancelQueries({
        queryKey: ['candidates-kanban', jobId],
      })
      const snapshot = queryClient.getQueryData<KanbanBoardResponse>([
        'candidates-kanban',
        jobId,
      ])
      if (snapshot) {
        queryClient.setQueryData<KanbanBoardResponse>(
          ['candidates-kanban', jobId],
          optimisticallyMoveCard(
            snapshot,
            vars.assignmentId,
            vars.targetStageId,
          ),
        )
      }
      return { snapshot }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) {
        queryClient.setQueryData(
          ['candidates-kanban', jobId],
          ctx.snapshot,
        )
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates-kanban', jobId],
      })
    },
  })
}

// Pure, testable helper — move one card from its current column to targetStageId.
// If the card can't be found, returns the board unchanged.
function optimisticallyMoveCard(
  board: KanbanBoardResponse,
  assignmentId: string,
  targetStageId: string,
): KanbanBoardResponse {
  let moved: KanbanCandidateCard | undefined
  const stages = board.stages.map((s) => {
    const kept = s.candidates.filter((c) => {
      if (c.assignment_id === assignmentId) {
        moved = { ...c, current_stage_id: targetStageId }
        return false
      }
      return true
    })
    return { ...s, candidates: kept }
  })
  if (!moved) return board
  const movedCard = moved
  return {
    ...board,
    stages: stages.map((s) =>
      s.stage_id === targetStageId
        ? { ...s, candidates: [...s.candidates, movedCard] }
        : s,
    ),
  }
}
