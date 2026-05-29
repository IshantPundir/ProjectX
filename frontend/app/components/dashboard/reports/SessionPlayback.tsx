'use client'

import { useEffect, useRef, useState } from 'react'

import type { RecordingTranscriptSegment } from '@/lib/api/reports'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'

/**
 * Index of the transcript segment that is "active" at `currentMs`, i.e. the
 * last segment whose start time is <= currentMs. Returns -1 before the first
 * segment. Pure + exported for unit testing.
 */
export function activeSegmentIndex(
  segments: RecordingTranscriptSegment[],
  currentMs: number,
): number {
  let idx = -1
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].t_ms <= currentMs) idx = i
    else break
  }
  return idx
}

function speakerLabel(role: string): string {
  return role === 'candidate' ? 'Candidate' : 'Interviewer'
}

const CARD = 'rounded-xl border bg-white p-3.5'

function PlaceholderFrame({ emoji, title, hint }: { emoji: string; title: string; hint: string }) {
  return (
    <div
      className="relative flex flex-col items-center justify-center rounded-lg"
      style={{ aspectRatio: '16 / 9', background: 'linear-gradient(160deg,#22323b,#0C2A38)', border: '1px dashed rgba(255,255,255,0.18)' }}
    >
      <span className="text-[30px]" aria-hidden="true">{emoji}</span>
      <span className="mt-1.5 text-[12px] font-semibold" style={{ color: '#c4d2d9' }}>{title}</span>
      <span className="mt-0.5 px-6 text-center text-[10.5px]" style={{ color: '#7d929c' }}>{hint}</span>
    </div>
  )
}

/**
 * Report-page session recording player: candidate video + mixed interview
 * audio, with a transcript rail that highlights/auto-scrolls in sync with
 * playback and lets the recruiter click any line to seek.
 *
 * Verbal-content-only scoring is unchanged — the recording is for human
 * review, not automated facial/affect analysis (see the badge).
 */
export function SessionPlayback({ sessionId }: { sessionId: string | null }) {
  const enabled = !!sessionId
  const { data, isLoading } = useSessionRecording(sessionId ?? '')

  const videoRef = useRef<HTMLVideoElement>(null)
  const railRef = useRef<HTMLOListElement>(null)
  const [activeIdx, setActiveIdx] = useState(-1)

  const transcript = data?.transcript ?? []
  const offsetMs = data?.offset_ms ?? 0

  // Keep the highlighted transcript line in view as playback advances.
  useEffect(() => {
    if (activeIdx < 0) return
    const row = railRef.current?.children[activeIdx] as HTMLElement | undefined
    row?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [activeIdx])

  function onTimeUpdate() {
    const v = videoRef.current
    if (!v) return
    setActiveIdx(activeSegmentIndex(transcript, v.currentTime * 1000 - offsetMs))
  }

  function seekTo(seg: RecordingTranscriptSegment) {
    const v = videoRef.current
    if (!v) return
    v.currentTime = Math.max(0, (seg.t_ms + offsetMs) / 1000)
    void v.play?.()
  }

  let frame: React.ReactNode
  if (!enabled || isLoading) {
    frame = <PlaceholderFrame emoji="🎬" title="Session playback" hint="Loading recording…" />
  } else if (data?.status === 'ready' && data.signed_url) {
    frame = (
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.6fr_1fr]">
        <video
          ref={videoRef}
          src={data.signed_url}
          controls
          playsInline
          onTimeUpdate={onTimeUpdate}
          aria-label="Interview session recording"
          className="w-full rounded-lg bg-black"
          style={{ aspectRatio: '16 / 9' }}
        />
        <div className="rounded-lg border" style={{ borderColor: 'var(--px-hairline)' }}>
          <div className="border-b px-3 py-1.5 text-[11px] font-semibold" style={{ borderColor: 'var(--px-hairline)', color: '#5b6b73' }}>
            Transcript
          </div>
          {transcript.length === 0 ? (
            <p className="px-3 py-3 text-[11px]" style={{ color: '#7d929c' }}>No transcript captured.</p>
          ) : (
            <ol ref={railRef} className="max-h-[260px] overflow-y-auto p-1.5" aria-label="Interview transcript">
              {transcript.map((seg, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => seekTo(seg)}
                    aria-current={i === activeIdx ? 'true' : undefined}
                    className="w-full rounded-md px-2 py-1.5 text-left text-[11.5px] transition-colors"
                    style={{
                      background: i === activeIdx ? 'var(--px-ai-bg)' : 'transparent',
                      color: i === activeIdx ? 'var(--px-ai)' : 'inherit',
                    }}
                  >
                    <span className="mr-1.5 text-[9.5px] font-semibold uppercase tracking-wide" style={{ color: '#8aa0ac' }}>
                      {speakerLabel(seg.role)}
                    </span>
                    {seg.text}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    )
  } else if (data?.status === 'recording') {
    frame = <PlaceholderFrame emoji="⏳" title="Recording is processing" hint="The session recording is still being prepared. This view will update automatically." />
  } else if (data?.status === 'failed') {
    frame = <PlaceholderFrame emoji="⚠️" title="Recording unavailable" hint="This session's recording could not be produced. The transcript and scores below are unaffected." />
  } else {
    frame = <PlaceholderFrame emoji="🎬" title="No recording" hint="No video recording is available for this session." />
  }

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      {frame}
      <VerbalContentOnlyBadge />
    </div>
  )
}

export function VerbalContentOnlyBadge() {
  return (
    <div className="mt-2.5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px]"
      style={{ color: 'var(--px-ai)', background: 'var(--px-ai-bg)', borderColor: 'var(--px-ai-line)' }}>
      🛈&nbsp;Verbal-content-only — scored on what the candidate said. No facial, affect, or appearance analysis.
    </div>
  )
}
