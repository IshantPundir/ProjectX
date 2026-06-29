'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { ApiError } from '@/lib/api/client'
import { useGenerateReel, useReel } from '@/lib/hooks/use-reel'
import { ReelTheater } from './theater/ReelTheater'

const CARD = 'rounded-xl border bg-white p-3.5'

/**
 * Highlights card for the report page. Owns the full generation lifecycle —
 * absent → generating → ready / failed — and plays the ready reel in a modal with
 * a chapter rail. The reel surfaces the video evidence behind the verdict; the
 * backend gates eligibility (report + recording ready, any verdict).
 */
export function ReelCard({
  sessionId,
  candidateName,
  verdict,
}: {
  sessionId: string
  candidateName: string
  verdict: 'advance' | 'borderline' | 'reject'
}) {
  const { data, isLoading } = useReel(sessionId)
  const generate = useGenerateReel(sessionId)
  const [playing, setPlaying] = useState(false)

  const trigger = (regenerate: boolean) =>
    generate.mutate(
      { regenerate },
      {
        onError: (err) =>
          toast.error(
            err instanceof ApiError && err.status === 422
              ? err.message
              : 'Could not start the reel. Please try again.',
          ),
      },
    )

  const status = data?.status ?? 'absent'
  const busy =
    status === 'pending' || status === 'generating' || generate.isPending

  const reelBlurb =
    verdict === 'advance' ? 'A ~60s reel: why this candidate fits.'
    : verdict === 'borderline' ? 'A ~60s reel: the case both ways.'
    : 'A ~60s reel: the evidence behind this call.'

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg-2)' }}>
          Highlights
        </span>
        {status === 'ready' && data?.duration_seconds != null && (
          <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
            {Math.round(data.duration_seconds)}s
          </span>
        )}
      </div>

      {isLoading ? (
        <Poster muted>Loading…</Poster>
      ) : status === 'ready' ? (
        <ReadyPoster
          candidateName={candidateName}
          onPlay={() => setPlaying(true)}
          onRegenerate={() => trigger(true)}
          regenerating={generate.isPending}
        />
      ) : busy ? (
        <Poster spinner>Generating Highlights… this takes a minute.</Poster>
      ) : status === 'failed' ? (
        <FailedState
          error={data?.generation_error}
          onRetry={() => trigger(true)}
          retrying={generate.isPending}
        />
      ) : data?.eligible ? (
        <EmptyState onGenerate={() => trigger(false)} starting={generate.isPending} blurb={reelBlurb} />
      ) : (
        <Poster muted>{data?.ineligible_reason ?? 'Reel not available yet.'}</Poster>
      )}

      {/* Mount only while playing (matches ReviewTheater in ReportView): the
          theater's fire-once `closing` state resets by remount, so reopening
          after a close comes up clean instead of stuck mid-exit. The exit
          animation still plays — onClose (the unmount) is deferred behind it. */}
      {playing && data?.signed_url && (
        <ReelTheater
          open
          signedUrl={data.signed_url}
          chapters={data.chapters ?? []}
          durationSeconds={data.duration_seconds ?? null}
          candidateName={candidateName}
          subtitle=""
          onClose={() => setPlaying(false)}
        />
      )}
    </div>
  )
}

function Poster({
  children,
  spinner,
  muted,
}: {
  children: React.ReactNode
  spinner?: boolean
  muted?: boolean
}) {
  return (
    <div
      className="flex w-full flex-col items-center justify-center gap-2 rounded-lg px-4 text-center"
      style={{
        aspectRatio: '16 / 9',
        background: 'radial-gradient(120% 100% at 50% 12%, #14101c, #0c1620 62%, #080e14)',
        border: '1px solid var(--px-hairline)',
      }}
    >
      {spinner && (
        <span
          className="h-7 w-7 animate-spin rounded-full border-2 border-white/25 border-t-white"
          aria-hidden="true"
        />
      )}
      <span
        className="text-[12px] font-medium"
        style={{ color: muted ? 'rgba(255,255,255,0.62)' : '#fff' }}
      >
        {children}
      </span>
    </div>
  )
}

function EmptyState({
  onGenerate,
  starting,
  blurb,
}: {
  onGenerate: () => void
  starting: boolean
  blurb: string
}) {
  return (
    <div
      className="flex w-full flex-col items-center justify-center gap-3 rounded-lg px-4 text-center"
      style={{
        aspectRatio: '16 / 9',
        background: 'radial-gradient(120% 100% at 50% 12%, #14101c, #0c1620 62%, #080e14)',
        border: '1px solid var(--px-hairline)',
      }}
    >
      <span className="text-[13px] font-semibold text-white">
        {blurb}
      </span>
      <span className="text-[11px]" style={{ color: 'rgba(255,255,255,0.6)' }}>
        AI-directed from the interview — the evidence behind the verdict, at a glance.
      </span>
      <Button size="sm" onClick={onGenerate} disabled={starting}>
        {starting ? 'Starting…' : 'Create Highlights'}
      </Button>
    </div>
  )
}

function FailedState({
  error,
  onRetry,
  retrying,
}: {
  error?: string | null
  onRetry: () => void
  retrying: boolean
}) {
  return (
    <div
      className="flex w-full flex-col items-center justify-center gap-3 rounded-lg px-4 text-center"
      style={{
        aspectRatio: '16 / 9',
        background: 'radial-gradient(120% 100% at 50% 12%, #1c1014, #160c10 62%, #0e0608)',
        border: '1px solid var(--px-hairline)',
      }}
    >
      <span className="text-[13px] font-semibold text-white">Reel generation failed</span>
      {error && (
        <span className="line-clamp-2 text-[11px]" style={{ color: 'rgba(255,255,255,0.6)' }}>
          {error}
        </span>
      )}
      <Button size="sm" variant="secondary" onClick={onRetry} disabled={retrying}>
        {retrying ? 'Retrying…' : 'Retry'}
      </Button>
    </div>
  )
}

function ReadyPoster({
  candidateName,
  onPlay,
  onRegenerate,
  regenerating,
}: {
  candidateName: string
  onPlay: () => void
  onRegenerate: () => void
  regenerating: boolean
}) {
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onPlay}
        aria-label={`Play ${candidateName}'s Highlights`}
        className="group relative flex w-full items-center justify-center overflow-hidden rounded-lg"
        style={{
          aspectRatio: '16 / 9',
          background: 'radial-gradient(120% 100% at 50% 12%, #14101c, #0c1620 62%, #080e14)',
          border: '1px solid var(--px-hairline)',
        }}
      >
        <span
          className="relative grid h-14 w-14 place-items-center rounded-full text-[20px] text-white transition-transform group-hover:scale-110"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        >
          ▶
        </span>
        <span
          className="absolute bottom-2.5 left-2.5 text-[11px] font-semibold text-white"
          style={{ textShadow: '0 1px 6px rgba(0,0,0,0.5)' }}
        >
          {candidateName} · Highlights
        </span>
      </button>
      <button
        type="button"
        onClick={onRegenerate}
        disabled={regenerating}
        className="text-[11px] underline disabled:opacity-50"
        style={{ color: 'var(--px-fg-4)' }}
      >
        {regenerating ? 'Regenerating…' : 'Regenerate'}
      </button>
    </div>
  )
}
