'use client'

import { useCallback, useMemo, useRef, useState } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import type { MomentSelection } from './ThisMomentPanel'
import { activeQuestionId, type FlagMarker, type TimelineMarker } from './timeline-model'

export function useTheaterState(params: {
  markers: TimelineMarker[]
  questions: QuestionOut[]
  durationMs: number
}) {
  const { markers, questions, durationMs } = params
  const seekRef = useRef<PlaybackSeekApi | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  // explicit selection overrides the playhead-derived active question until cleared
  const [explicit, setExplicit] = useState<MomentSelection>(null)

  const playheadActiveId = useMemo(() => activeQuestionId(markers, currentMs), [markers, currentMs])

  const selection: MomentSelection = explicit
  const activeId =
    explicit?.type === 'question' ? explicit.question.question_id : playheadActiveId

  const seekMs = useCallback((ms: number) => {
    seekRef.current?.seekToMs(ms)
  }, [])

  const selectQuestion = useCallback((questionId: string) => {
    const q = questions.find((x) => x.question_id === questionId)
    if (!q) return
    setExplicit({ type: 'question', question: q })
    if (q.asked_at_ms != null) seekRef.current?.seekToMs(q.asked_at_ms)
  }, [questions])

  const selectFlag = useCallback((flag: FlagMarker) => {
    setExplicit({ type: 'flag', flag })
    seekRef.current?.seekToMs(flag.startMs)
  }, [])

  const clearSelection = useCallback(() => setExplicit(null), [])

  const playheadPct = durationMs > 0 ? Math.min(100, (currentMs / durationMs) * 100) : 0

  return {
    seekRef,
    currentMs,
    setCurrentMs,
    selection,
    activeId,
    playheadPct,
    seekMs,
    selectQuestion,
    selectFlag,
    clearSelection,
  }
}
