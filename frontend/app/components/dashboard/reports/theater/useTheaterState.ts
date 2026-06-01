'use client'

import { useCallback, useMemo, useState, type MutableRefObject } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import type { MomentSelection } from './ThisMomentPanel'
import { activeQuestionId, type FlagMarker, type TimelineMarker } from './timeline-model'

/**
 * Selection/playhead state for the theater. `seekRef` and `currentMs` are passed
 * in (owned by ReviewTheater) so the video controller — which writes them — can
 * be created BEFORE this hook, letting the controller's intrinsic duration feed
 * marker/flag positioning as a pure render-time derivation (no setState-in-effect).
 */
export function useTheaterState(params: {
  markers: TimelineMarker[]
  questions: QuestionOut[]
  durationMs: number
  seekRef: MutableRefObject<PlaybackSeekApi | null>
  currentMs: number
}) {
  const { markers, questions, durationMs, seekRef, currentMs } = params
  // explicit selection overrides the playhead-derived active question until cleared
  const [explicit, setExplicit] = useState<MomentSelection>(null)

  const playheadActiveId = useMemo(() => activeQuestionId(markers, currentMs), [markers, currentMs])

  const selection: MomentSelection = explicit
  const activeId =
    explicit?.type === 'question' ? explicit.question.question_id : playheadActiveId

  const seekMs = useCallback((ms: number) => {
    seekRef.current?.seekToMs(ms)
  }, [seekRef])

  const selectQuestion = useCallback((questionId: string) => {
    const q = questions.find((x) => x.question_id === questionId)
    if (!q) return
    setExplicit({ type: 'question', question: q })
    if (q.asked_at_ms != null) seekRef.current?.seekToMs(q.asked_at_ms)
  }, [questions, seekRef])

  const selectFlag = useCallback((flag: FlagMarker) => {
    setExplicit({ type: 'flag', flag })
    seekRef.current?.seekToMs(flag.startMs)
  }, [seekRef])

  const clearSelection = useCallback(() => setExplicit(null), [])

  const playheadPct = durationMs > 0 ? Math.min(100, (currentMs / durationMs) * 100) : 0

  return {
    selection,
    activeId,
    playheadPct,
    seekMs,
    selectQuestion,
    selectFlag,
    clearSelection,
  }
}
