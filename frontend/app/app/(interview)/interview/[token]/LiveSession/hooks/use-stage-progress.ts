'use client'

import { useParticipants } from '@livekit/components-react'

export interface StageProgress {
  currentQuestion: number
  totalQuestions: number
  timeRemainingSeconds: number
}

export function useStageProgress(): StageProgress | null {
  const participants = useParticipants()
  const agent = participants.find((p) => p.identity.startsWith('agent-'))
  if (!agent) return null
  const a = agent.attributes ?? {}
  const cur = parseInt(a['current_question_index'] ?? '')
  const total = parseInt(a['total_questions'] ?? '')
  const tRemain = parseInt(a['time_remaining_seconds'] ?? '')
  if (Number.isNaN(cur) || Number.isNaN(total) || Number.isNaN(tRemain)) return null
  return { currentQuestion: cur, totalQuestions: total, timeRemainingSeconds: tRemain }
}
