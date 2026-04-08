'use client'

import { Skeleton } from '@/components/ui/skeleton'
import type { JobStatusEvent } from '@/lib/api/jobs'

type Props = {
  status: JobStatusEvent | null
}

/**
 * Content-aware skeleton — status pill bound to SSE events, section
 * labels pre-rendered so the transition to real content feels like
 * filling in blanks rather than a full swap.
 */
export function LoadingSkeleton({ status }: Props) {
  const statusText =
    status?.status === 'signals_extracting'
      ? 'Extracting signals and enriching JD…'
      : 'Dispatching extraction job…'

  return (
    <div className="grid grid-cols-1 3xl:grid-cols-[1fr_2fr_1.2fr] gap-4 min-h-[60vh]">
      {/* Left: Original JD skeleton — hidden below 3xl */}
      <aside className="hidden 3xl:block bg-white rounded-lg border border-zinc-200 p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-3">
          Original JD
        </h3>
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-[75%] mb-2" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[60%]" />
      </aside>

      {/* Center: Enriched JD skeleton with status pill */}
      <section className="bg-white rounded-lg border border-zinc-200 p-6">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-4 pb-2 border-b border-zinc-100">
          Enriched JD
        </h3>
        <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 text-xs px-3 py-1.5 rounded-full border border-blue-200 mb-4">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          {statusText}
        </div>
        <Skeleton className="h-4 w-[40%] mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-[75%] mb-6" />
        <Skeleton className="h-4 w-[35%] mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-full" />
      </section>

      {/* Right: Signals skeleton with section labels pre-rendered */}
      <aside className="bg-white rounded-lg border border-zinc-200 p-5 space-y-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 pb-2 border-b border-zinc-100">
          Signals
        </h3>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Role Summary
          </div>
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-[80%]" />
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Required Skills
          </div>
          <div className="flex gap-1.5 flex-wrap">
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Must Haves
          </div>
          <div className="flex gap-1.5 flex-wrap">
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        </div>
      </aside>
    </div>
  )
}
