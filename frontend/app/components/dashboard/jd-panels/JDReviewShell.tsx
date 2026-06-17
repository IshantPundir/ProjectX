'use client'

import { useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'
import { useSaveSignals } from '@/lib/hooks/use-save-signals'
import { useReExtractSignals } from '@/lib/hooks/use-re-extract-signals'
import type { JobPostingWithSnapshot, SignalItem } from '@/lib/api/jobs'

import { DangerConfirmDialog, Tabs } from '@/components/px'
import { EnrichedJdCanvas } from './EnrichedJdCanvas'
import { RawJdCanvas } from './RawJdCanvas'
import { InspectorHint } from './components/InspectorHint'
import { InspectorTips } from './components/InspectorTips'
import { SectionsRail } from './SectionsRail'
import { SignalInspector } from './SignalInspector'
import { SignalsCanvas } from './SignalsCanvas'
import { groupSignals } from './helpers/groupSignals'
import { needsReview } from './helpers/needsReview'

type InnerView = 'raw' | 'enriched' | 'signals'

const VALID_VIEWS: InnerView[] = ['raw', 'enriched', 'signals']

export function JDReviewShell({
  job,
  onReEnrich,
}: {
  job: JobPostingWithSnapshot
  onReEnrich: () => void
}) {
  const searchParams = useSearchParams()
  const router = useRouter()

  const snapshot = job.latest_snapshot!
  const [signals, setSignals] = useState<SignalItem[]>(snapshot.signals)
  const [isDirty, setIsDirty] = useState(false)

  const rawView = searchParams.get('view')
  const view: InnerView = (
    rawView && (VALID_VIEWS as string[]).includes(rawView) ? rawView : 'signals'
  ) as InnerView

  const focusIdxParam = searchParams.get('signal')
  const focusIdx = focusIdxParam ? Number(focusIdxParam) : null

  const setView = (v: InnerView) => {
    const qs = new URLSearchParams(searchParams.toString())
    if (v === 'signals') qs.delete('view')
    else qs.set('view', v)
    qs.set('tab', 'jd')
    router.replace(`/jobs/${job.id}?${qs.toString()}`, { scroll: false })
  }

  const setFocus = (idx: number | null) => {
    const qs = new URLSearchParams(searchParams.toString())
    if (idx === null) qs.delete('signal')
    else qs.set('signal', String(idx))
    qs.set('tab', 'jd')
    router.replace(`/jobs/${job.id}?${qs.toString()}`, { scroll: false })
  }

  const { must, nice } = useMemo(() => groupSignals(signals), [signals])

  const needsReviewCount = signals.filter(needsReview).length
  const totalCount = signals.length

  const [activeSection, setActiveSection] = useState<
    'must' | 'nice' | 'snapshot'
  >(must.length > 0 ? 'must' : nice.length > 0 ? 'nice' : 'snapshot')

  const saveMutation = useSaveSignals(job.id)
  const confirmMutation = useConfirmSignals(job.id)
  const reExtract = useReExtractSignals(job.id)
  const [confirmReExtract, setConfirmReExtract] = useState(false)

  const updateSignal = (index: number, patch: Partial<SignalItem>) => {
    setSignals((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
    setIsDirty(true)
  }
  const removeSignal = (index: number) => {
    setSignals((prev) => prev.filter((_, i) => i !== index))
    setIsDirty(true)
    setFocus(null)
  }

  const save = () => {
    saveMutation.mutate(
      {
        signals,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
      {
        onSuccess: () => setIsDirty(false),
      },
    )
  }

  const saveAndConfirm = () => {
    if (!isDirty) {
      confirmMutation.mutate()
      return
    }
    saveMutation.mutate(
      {
        signals,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
      {
        onSuccess: () => {
          setIsDirty(false)
          confirmMutation.mutate()
        },
      },
    )
  }

  const focusSignal = focusIdx != null ? signals[focusIdx] : null

  // Overall state chip
  const isConfirmed = job.is_confirmed
  const stateBanner = needsReviewCount >= 4 ? 'low-confidence' : null
  const canManage = job.can_manage

  return (
    // items-stretch (default) is load-bearing: sticky children inside a grid
    // pin within their grid cell. If the cell is sized to content (items-start),
    // there's no track for sticky to traverse — the panel just sits in flow.
    <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
      <SectionsRail
        must={must}
        nice={nice}
        hasSnapshot={!!snapshot.role_summary || !!snapshot.seniority_level}
        totalCount={totalCount}
        needsReviewCount={needsReviewCount}
        activeSection={view !== 'signals' ? null : activeSection}
        onJump={(target) => {
          const wasNonSignals = view !== 'signals'
          if (wasNonSignals) setView('signals')
          setActiveSection(target)

          const run = () => {
            const elId =
              target === 'must'
                ? 'sig-group-must'
                : target === 'nice'
                  ? 'sig-group-nice'
                  : 'sig-group-snapshot'
            const el = document.getElementById(elId)
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }

          if (wasNonSignals) {
            requestAnimationFrame(() => requestAnimationFrame(run))
          } else {
            run()
          }
        }}
      />

      {/* Center column: Tabs toggle + canvas */}
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center justify-between">
          <Tabs<InnerView>
            ariaLabel="JD view"
            value={view}
            onChange={setView}
            items={[
              { value: 'raw', label: 'Raw JD' },
              {
                value: 'enriched',
                label: 'Enriched JD',
                hidden: !job.description_enriched && job.enrichment_status !== 'streaming',
                disabled: job.enrichment_status === 'failed',
                disabledHint: 'Enrichment failed — retry to re-run',
              },
              { value: 'signals', label: 'Signal details' },
            ]}
          />
        </div>

        {view === 'raw' ? (
          <RawJdCanvas job={job} />
        ) : view === 'enriched' ? (
          <EnrichedJdCanvas job={job} onReEnrich={onReEnrich} />
        ) : (
          <SignalsCanvas
            must={must}
            nice={nice}
            job={job}
            stateBanner={stateBanner}
            isConfirmed={isConfirmed}
            canManage={canManage}
            isDirty={isDirty}
            saving={saveMutation.isPending}
            confirming={confirmMutation.isPending}
            totalCount={totalCount}
            focusIdx={focusIdx}
            onFocus={setFocus}
            onSave={save}
            onSaveAndConfirm={saveAndConfirm}
            onReEnrich={onReEnrich}
            onReExtract={() => setConfirmReExtract(true)}
            reExtracting={reExtract.isPending}
          />
        )}
      </div>

      {view !== 'signals' ? (
        <InspectorTips />
      ) : focusSignal ? (
        <SignalInspector
          signal={focusSignal}
          signalIndex={focusIdx!}
          jobRaw={job.description_raw}
          canManage={canManage}
          onUpdate={(patch) => updateSignal(focusIdx!, patch)}
          onRemove={() => removeSignal(focusIdx!)}
        />
      ) : (
        <InspectorHint
          needsReviewCount={needsReviewCount}
          isConfirmed={isConfirmed}
        />
      )}

      <DangerConfirmDialog
        open={confirmReExtract}
        title="Re-enrich & re-extract?"
        description="This regenerates the enriched JD from the raw JD, then re-extracts fresh signals from it and clears the question banks generated from the old signals. You'll review the new signals and regenerate the banks. The job resets to signal review."
        confirmLabel="Unlock & re-enrich"
        onConfirm={() => { setConfirmReExtract(false); reExtract.mutate() }}
        onClose={() => setConfirmReExtract(false)}
      />
    </div>
  )
}
