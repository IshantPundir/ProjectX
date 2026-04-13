'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'

import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import { QuestionSidebar } from './QuestionSidebar'
import { QuestionsMainPane } from './QuestionsMainPane'

export function QuestionsReviewContent() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const [explicitStageId, setExplicitStageId] = useState<string | null>(null)

  const { data: overview, isLoading } = useBanksOverview(jobId)

  // Derive the effective selection: the explicit user choice wins, otherwise
  // fall back to the first stage in the overview. Deriving this (instead of
  // syncing via useEffect + setState) avoids a cascading render and satisfies
  // react-hooks/set-state-in-effect.
  const effectiveStageId =
    explicitStageId ?? overview?.banks[0]?.stage_id ?? null

  useQuestionsStatusStream(jobId, effectiveStageId)

  if (isLoading) {
    return <div className="p-8 text-sm text-zinc-500">Loading banks…</div>
  }

  return (
    <div className="flex h-full min-h-[600px]">
      <QuestionSidebar
        banks={overview?.banks ?? []}
        selectedStageId={effectiveStageId}
        onSelect={setExplicitStageId}
      />
      <div className="flex-1 overflow-y-auto">
        {effectiveStageId ? (
          <QuestionsMainPane jobId={jobId} stageId={effectiveStageId} />
        ) : (
          <div className="p-8 text-sm text-zinc-500">
            Select a stage from the sidebar to view its question bank.
          </div>
        )}
      </div>
    </div>
  )
}
