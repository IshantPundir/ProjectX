'use client'

import { useCallback, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { ApiError } from '@/lib/api/client'
import type { ReelChapter } from '@/lib/api/reels'
import { useGenerateReel, useReel } from '@/lib/hooks/use-reel'

const CARD = 'rounded-xl border bg-white p-3.5'

/**
 * Candidate Reel card for the report page. Owns the full generation lifecycle —
 * absent → generating → ready / failed — and plays the ready reel in a modal with
 * a chapter rail. The reel is a positive highlight that ships alongside the full
 * report + recording; the backend gates eligibility (advance/borderline + ready).
 */
export function ReelCard({
  sessionId,
  candidateName,
}: {
  sessionId: string
  candidateName: string
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

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg-2)' }}>
          Candidate reel
        </span>
        {status === 'ready' && data?.duration_seconds != null && (
          <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
            {Math.round(data.duration_seconds)}s highlight
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
        <Poster spinner>Generating reel… this takes a minute.</Poster>
      ) : status === 'failed' ? (
        <FailedState
          error={data?.generation_error}
          onRetry={() => trigger(true)}
          retrying={generate.isPending}
        />
      ) : data?.eligible ? (
        <EmptyState onGenerate={() => trigger(false)} starting={generate.isPending} />
      ) : (
        <Poster muted>{data?.ineligible_reason ?? 'Reel not available yet.'}</Poster>
      )}

      {playing && data?.signed_url && (
        <ReelPlayerModal
          src={data.signed_url}
          chapters={data.chapters}
          candidateName={candidateName}
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

function EmptyState({ onGenerate, starting }: { onGenerate: () => void; starting: boolean }) {
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
        A ~60s highlight reel for this candidate
      </span>
      <span className="text-[11px]" style={{ color: 'rgba(255,255,255,0.6)' }}>
        AI-directed from the interview — the case for advancing, at a glance.
      </span>
      <Button size="sm" onClick={onGenerate} disabled={starting}>
        {starting ? 'Starting…' : 'Create candidate reel'}
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
        aria-label={`Play ${candidateName}'s candidate reel`}
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
          {candidateName} · highlight reel
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

function ReelPlayerModal({
  src,
  chapters,
  candidateName,
  onClose,
}: {
  src: string
  chapters: ReelChapter[]
  candidateName: string
  onClose: () => void
}) {
  // Callback ref (not a ref object): the element is the dependency, so chapter
  // seeking always targets the live <video> node. See feedback_dialog_portal_node_ref.
  const [video, setVideo] = useState<HTMLVideoElement | null>(null)
  const seek = useCallback(
    (ms: number) => {
      if (video) {
        video.currentTime = ms / 1000
        void video.play()
      }
    },
    [video],
  )

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`${candidateName} candidate reel`}
      onClick={onClose}
    >
      <div
        className="w-full max-w-[860px] overflow-hidden rounded-2xl bg-black"
        onClick={(e) => e.stopPropagation()}
      >
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          key={src}
          ref={setVideo}
          src={src}
          controls
          autoPlay
          className="aspect-video w-full bg-black"
        />
        {chapters.length > 0 && (
          <div className="flex flex-wrap gap-1.5 bg-black/95 p-3">
            {chapters.map((c, i) => (
              <button
                key={i}
                type="button"
                onClick={() => seek(c.start_ms)}
                className="rounded-full px-2.5 py-1 text-[11px] text-white/80 transition-colors hover:bg-white/15"
                style={{ border: '1px solid rgba(255,255,255,0.18)' }}
              >
                {c.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close reel"
        className="absolute right-5 top-5 grid h-9 w-9 place-items-center rounded-full bg-white/15 text-white hover:bg-white/25"
      >
        ✕
      </button>
    </div>
  )
}
