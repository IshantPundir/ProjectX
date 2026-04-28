'use client'

import { useState } from 'react'

import { Skeleton, Tabs } from '@/components/px'
import type { EnrichmentStatus } from '@/lib/api/jobs'

type Props = {
  descriptionRaw: string
  descriptionEnriched?: string | null
  enrichmentStatus: EnrichmentStatus
  skipEnrichment: boolean
  sseError?: string | null
}

type View = 'raw' | 'enriched'

/**
 * Loading view rendered while a job is in `signals_extracting` state.
 *
 * Layout mirrors JDReviewShell (3-column grid). The center column hosts a
 * Tabs control identical to the review shell's; the loading state is
 * phase-targeted:
 *
 * - Phase 1 (enrichment_status='streaming'): center "Enriched JD" tab shows
 *   skeleton; side panels show a static "Waiting for signals…" placeholder
 *   (no shimmer).
 * - Phase 2 (enrichment_status='completed' or 'idle'+skipEnrichment): center
 *   shows the JD that the model is using; side panels show signal-loading
 *   skeletons.
 *
 * After phase 2 completes, the parent page swaps this component for
 * JDReviewShell.
 */
export function JDExtractingView({
  descriptionRaw,
  descriptionEnriched,
  enrichmentStatus,
  skipEnrichment,
  sseError,
}: Props) {
  // Default tab logic — see spec §5.2 default tab matrix.
  const computeDefaultView = (): View => {
    if (skipEnrichment) return 'raw'
    if (enrichmentStatus === 'streaming') return 'enriched'
    if (enrichmentStatus === 'completed') return 'enriched'
    if (enrichmentStatus === 'failed') return 'raw'
    return 'raw'
  }
  const [view, setView] = useState<View>(computeDefaultView())

  // Phase-targeted state flags
  const phase1InFlight = enrichmentStatus === 'streaming'
  const phase2InFlight = !phase1InFlight && enrichmentStatus !== 'failed'

  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
      {/* Left rail — quiet during extraction. */}
      <aside
        className="rounded-[10px] border p-4 self-start"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="px-eyebrow mb-3"
          style={{ marginBottom: 12, color: 'var(--px-fg-3)' }}
        >
          Sections
        </div>
        {phase2InFlight ? (
          <>
            <Skeleton className="h-3 w-full mb-2" />
            <Skeleton className="h-3 w-3/4 mb-2" />
            <Skeleton className="h-3 w-2/3" />
          </>
        ) : (
          <div
            className="text-[12.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            Waiting for signals…
          </div>
        )}
      </aside>

      {/* Center column — Tabs + body */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <Tabs<View>
            ariaLabel="JD view"
            value={view}
            onChange={setView}
            items={[
              { value: 'raw', label: 'Raw JD' },
              {
                value: 'enriched',
                label: 'Enriched JD',
                hidden: skipEnrichment,
                disabled: enrichmentStatus === 'failed',
                disabledHint: 'Enrichment failed — retry to re-run',
              },
            ]}
          />
          {sseError && (
            <span
              className="text-[12px] px-2 py-1 rounded border"
              style={{
                background: 'var(--px-caution-bg)',
                borderColor: 'var(--px-caution-line)',
                color: 'var(--px-caution)',
              }}
            >
              {sseError}
            </span>
          )}
        </div>

        {view === 'raw' && (
          <div
            data-testid="jd-center-raw-body"
            className="rounded-[10px] border p-6 whitespace-pre-wrap text-[13.5px]"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
              color: 'var(--px-fg-2)',
              lineHeight: 1.65,
            }}
          >
            {descriptionRaw}
          </div>
        )}

        {view === 'enriched' && (
          phase1InFlight ? (
            <div
              data-testid="jd-center-loading-enrichment"
              className="rounded-[10px] border p-6 space-y-3"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
              }}
            >
              <div
                className="inline-flex items-center gap-2 text-[12px] mb-3"
                style={{ color: 'var(--px-accent)' }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full animate-pulse"
                  style={{ background: 'var(--px-accent)' }}
                />
                Copilot is enriching the JD…
              </div>
              <Skeleton className="h-4 w-1/3 mb-2" />
              <Skeleton className="h-3 w-full mb-1.5" />
              <Skeleton className="h-3 w-11/12 mb-1.5" />
              <Skeleton className="h-3 w-3/4 mb-4" />
              <Skeleton className="h-4 w-2/5 mb-2" />
              <Skeleton className="h-3 w-full mb-1.5" />
              <Skeleton className="h-3 w-5/6" />
            </div>
          ) : (
            <div
              data-testid="jd-center-enriched-body"
              className="rounded-[10px] border p-6 whitespace-pre-wrap text-[13.5px]"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
                color: 'var(--px-fg-2)',
                lineHeight: 1.65,
              }}
            >
              {descriptionEnriched ?? ''}
            </div>
          )
        )}


      </section>

      {/* Right panel */}
      <aside
        className="rounded-[10px] border p-4 self-start"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
        data-testid={
          phase2InFlight ? 'jd-side-panel-skeleton' : 'jd-side-panel-waiting'
        }
      >
        <div
          className="px-eyebrow mb-3"
          style={{ marginBottom: 12, color: 'var(--px-fg-3)' }}
        >
          Signal inspector
        </div>
        {phase2InFlight ? (
          <div className="space-y-3">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-5/6" />
            <Skeleton className="h-3 w-2/3" />
            <div className="flex gap-1.5 flex-wrap pt-2">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-20 rounded-full" />
              <Skeleton className="h-5 w-14 rounded-full" />
            </div>
          </div>
        ) : (
          <div
            className="text-[12.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            Waiting for signals…
          </div>
        )}
      </aside>
    </div>
  )
}
