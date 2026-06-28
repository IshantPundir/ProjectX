'use client'

import { useCallback, useMemo, type MutableRefObject } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import type { MomentSelection } from './ThisMomentPanel'
import { activeQuestionId, type TimelineMarker } from './timeline-model'

/**
 * Playhead-derived state for the theater. `seekRef` and `currentMs` are passed in
 * (owned by ReviewTheater) so the video controller — which writes them — can be
 * created BEFORE this hook, letting the controller's intrinsic duration feed
 * marker/active derivation purely at render time (no setState-in-effect).
 *
 * Everything tracks the timeline: the active question (the latest whose
 * asked_at_ms has passed) drives BOTH the pill highlight AND the "This moment"
 * panel, so both advance during playback and manual scrubbing. Clicking a pill
 * just seeks to it — the playhead move makes it active, and thus shown.
 */
export function useTheaterState(params: {
  markers: TimelineMarker[]
  questions: QuestionOut[]
  durationMs: number
  seekRef: MutableRefObject<PlaybackSeekApi | null>
  currentMs: number
}) {
  const { markers, questions, seekRef, currentMs } = params

  const activeId = useMemo(() => activeQuestionId(markers, currentMs), [markers, currentMs])

  // "This moment" follows the playhead-active question (null before the first
  // one is reached → the panel shows the decision summary).
  const selection: MomentSelection = useMemo(() => {
    if (!activeId) return null
    const q = questions.find((x) => x.question_id === activeId)
    return q ? { type: 'question', question: q } : null
  }, [activeId, questions])

  const seekMs = useCallback((ms: number) => {
    seekRef.current?.seekToMs(ms)
  }, [seekRef])

  const selectQuestion = useCallback((questionId: string) => {
    const q = questions.find((x) => x.question_id === questionId)
    if (q?.asked_at_ms != null) seekRef.current?.seekToMs(q.asked_at_ms)
  }, [questions, seekRef])

  return {
    selection,
    activeId,
    seekMs,
    selectQuestion,
  }
}
