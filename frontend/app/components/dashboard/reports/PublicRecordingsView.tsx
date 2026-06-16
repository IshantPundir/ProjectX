'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { BrandLogo, Button, Skeleton } from '@/components/px'
import { reportsApi } from '@/lib/api/reports'
import { PublicPlaybackProvider } from '@/lib/hooks/public-playback-context'
import { ReelTheater } from './theater/ReelTheater'
import { ReviewTheater } from './theater/ReviewTheater'

/**
 * Public, token-gated playback surface. Fetches the full envelope from the
 * public API (no auth), then lets an external recruiter watch the full session
 * (ReviewTheater: video + proctoring + scores + transcript + decision) and the
 * highlight reel. The recruiter REPORT page stays private — this only plays back
 * the videos a shared PDF points at.
 */
export function PublicRecordingsView({ token }: { token: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public-recordings', token],
    queryFn: ({ signal }) => reportsApi.getPublicRecordings(token, { signal }),
    retry: false,
  })
  const [theaterOpen, setTheaterOpen] = useState(false)
  const [reelOpen, setReelOpen] = useState(false)

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
  const subtitle = `${data.job_title} · ${data.stage_label}`

  return (
    <PublicPlaybackProvider
      value={{ recording: data.recording, proctoring: data.proctoring }}
    >
      <div className="mx-auto max-w-2xl p-8">
        <BrandLogo className="mb-8 h-8" />
        <div className="rounded-2xl border bg-white p-8 shadow-sm">
          {data.report.reference_photo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={data.report.reference_photo_url}
              alt={data.candidate_name}
              className="mb-4 h-20 w-20 rounded-full object-cover"
            />
          ) : null}
          <h1 className="text-xl font-semibold">{data.candidate_name}</h1>
          <p className="text-sm text-muted-foreground">{subtitle}</p>

          <div className="mt-6 flex flex-col gap-3">
            <Button onClick={() => setTheaterOpen(true)}>
              ▶ Watch full session
            </Button>
            {reelReady ? (
              <Button variant="secondary" onClick={() => setReelOpen(true)}>
                ✨ Watch highlight reel
              </Button>
            ) : null}
          </div>
        </div>
      </div>

      <ReviewTheater
        open={theaterOpen}
        report={data.report}
        candidateName={data.candidate_name}
        subtitle={subtitle}
        onClose={() => setTheaterOpen(false)}
      />
      {reelReady ? (
        <ReelTheater
          open={reelOpen}
          signedUrl={data.reel.signed_url}
          chapters={data.reel.chapters}
          durationSeconds={data.reel.duration_seconds}
          candidateName={data.candidate_name}
          subtitle={subtitle}
          onClose={() => setReelOpen(false)}
        />
      ) : null}
    </PublicPlaybackProvider>
  )
}
