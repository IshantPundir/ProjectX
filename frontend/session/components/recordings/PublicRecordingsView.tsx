'use client'

import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'next/navigation'
import { useState } from 'react'

import { BrandLogo, Skeleton } from '@/components/recordings/px'
import { reportsApi } from '@/components/recordings/api/reports'
import { cn } from '@/lib/utils'
import { PublicPlaybackProvider } from '@/components/recordings/hooks/public-playback-context'
import { ReelTheater } from './theater/ReelTheater'
import { ReviewTheater } from './theater/ReviewTheater'

type Mode = 'reel' | 'full'

/**
 * Public, token-gated playback surface. Fetches the full envelope from the
 * public API (no auth), then plays the videos inline. The page lands directly
 * on the highlight reel (when one exists) and offers a top-left switch between
 * "Highlight reel" and "Full session" (the full ReviewTheater: video +
 * proctoring + scores + transcript + decision). When there is no reel, the
 * switch is omitted and the page shows Full session directly.
 *
 * Deep-linking: a `?view=reel` or `?view=full` query param picks the initial
 * view (the shared PDF's "Candidate highlight" and "Full session" buttons use
 * this). Absent/invalid → the reel-first default. A `?view=reel` request with no
 * reel available falls back to Full session (see activeMode below).
 *
 * The recruiter REPORT page stays private — this only plays back the videos a
 * shared PDF points at.
 */
export function PublicRecordingsView({ token }: { token: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public-recordings', token],
    queryFn: ({ signal }) => reportsApi.getPublicRecordings(token, { signal }),
    retry: false,
  })
  // Seed from ?view= when the PDF deep-links a specific view; otherwise null =
  // "not chosen yet" → derive the default from the data once it loads (reel-first),
  // without a flash or a stale initial value.
  const viewParam = useSearchParams().get('view')
  const initialMode: Mode | null =
    viewParam === 'reel' || viewParam === 'full' ? viewParam : null
  const [mode, setMode] = useState<Mode | null>(initialMode)

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl p-10">
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-md p-10 text-center">
        <BrandLogo className="mx-auto mb-6 h-8" />
        <h1 className="text-lg font-semibold">This link is no longer available</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The shared recording link may have expired or been revoked. Ask your
          contact to share it again.
        </p>
      </div>
    )
  }

  const reelReady = data.reel.status === 'ready'
  // No reel → always Full session (guards a ?view=reel deep-link with no reel,
  // which would otherwise open neither theater).
  const activeMode: Mode = reelReady ? (mode ?? 'reel') : 'full'
  const subtitle = `${data.job_title} · ${data.stage_label}`

  return (
    <PublicPlaybackProvider
      value={{ recording: data.recording, proctoring: data.proctoring }}
    >
      {/* Top-left switch — only when there's actually a reel to switch to. */}
      {reelReady ? (
        <div
          role="tablist"
          aria-label="Switch view"
          className="fixed left-4 top-4 z-[60] flex items-center gap-1 rounded-full border bg-white/90 p-1 shadow-sm backdrop-blur"
        >
          {(['reel', 'full'] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={activeMode === m}
              onClick={() => setMode(m)}
              className={cn(
                'rounded-full px-3.5 py-1.5 text-xs font-semibold transition-colors',
                activeMode === m
                  ? 'bg-zinc-900 text-white'
                  : 'text-zinc-600 hover:text-zinc-900',
              )}
            >
              {m === 'reel' ? 'Highlight reel' : 'Full session'}
            </button>
          ))}
        </div>
      ) : null}

      {reelReady ? (
        <ReelTheater
          open={activeMode === 'reel'}
          signedUrl={data.reel.signed_url}
          chapters={data.reel.chapters}
          durationSeconds={data.reel.duration_seconds}
          candidateName={data.candidate_name}
          subtitle={subtitle}
          showClose={false}
          onClose={() => {}}
        />
      ) : null}

      <ReviewTheater
        open={activeMode === 'full'}
        report={data.report}
        candidateName={data.candidate_name}
        subtitle={subtitle}
        showClose={false}
        onClose={() => {}}
      />
    </PublicPlaybackProvider>
  )
}
