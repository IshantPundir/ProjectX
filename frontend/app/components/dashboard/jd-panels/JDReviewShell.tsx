'use client'

import { useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'
import { useSaveSignals } from '@/lib/hooks/use-save-signals'
import type { JobPostingWithSnapshot, SignalItem } from '@/lib/api/jobs'

import { FullJdCanvas } from './FullJdCanvas'
import { InspectorHint } from './components/InspectorHint'
import { InspectorTips } from './components/InspectorTips'
import { SectionsRail } from './SectionsRail'
import { SignalInspector } from './SignalInspector'
import { SignalsCanvas } from './SignalsCanvas'
import { groupSignals } from './helpers/groupSignals'
import { needsReview } from './helpers/needsReview'

type InnerView = 'signals' | 'jd'

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

  const view = (searchParams.get('view') ?? 'signals') as InnerView
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
    'must' | 'nice' | 'snapshot' | 'jd'
  >(must.length > 0 ? 'must' : nice.length > 0 ? 'nice' : 'snapshot')

  const saveMutation = useSaveSignals(job.id)
  const confirmMutation = useConfirmSignals(job.id)

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
        activeSection={view === 'jd' ? 'jd' : activeSection}
        filename={`jd-v${snapshot.version}.txt`}
        onShowJd={() => {
          setView('jd')
          setActiveSection('jd')
        }}
        onJump={(target) => {
          if (target === 'jd') {
            setView('jd')
            setActiveSection('jd')
            return
          }

          const wasJd = view === 'jd'
          if (wasJd) setView('signals')
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

          if (wasJd) {
            requestAnimationFrame(() => requestAnimationFrame(run))
          } else {
            run()
          }
        }}
      />

      {view === 'jd' ? (
        <FullJdCanvas job={job} onReEnrich={onReEnrich} />
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
          needsReviewCount={needsReviewCount}
          totalCount={totalCount}
          focusIdx={focusIdx}
          onFocus={setFocus}
          onSave={save}
          onSaveAndConfirm={saveAndConfirm}
          onReEnrich={onReEnrich}
        />
      )}

      {view === 'jd' ? (
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
    </div>
  )
}
