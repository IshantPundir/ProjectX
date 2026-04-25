'use client'

import { FileQuestion, Settings } from 'lucide-react'
import { QuestionsMainPane } from '@/components/dashboard/question-bank/QuestionsMainPane'
import { StageConfigurationTab } from './StageConfigurationTab'
import { EmptyInspectorState } from './EmptyInspectorState'
import type { PipelineStageUpdateInput } from '@/lib/api/pipelines'

type Props = {
  jobId: string
  selectedStage: PipelineStageUpdateInput | null
  selectedStageIndex: number
  activeTab: 'questions' | 'config'
  onTabChange: (tab: 'questions' | 'config') => void
  onStageChange: (stage: PipelineStageUpdateInput) => void
}

export function StageInspectorPanel({
  jobId,
  selectedStage,
  activeTab,
  onTabChange,
  onStageChange,
}: Props) {
  const stageId = selectedStage?.id ?? null
  const hasSelection = selectedStage !== null && stageId !== null

  return (
    <div
      className="flex-1 rounded-r-xl flex flex-col min-w-0"
      style={{ background: 'var(--px-surface)' }}
      data-inspector-anchor="true"
    >
      {/* Tab bar */}
      <div
        className="border-b flex items-center gap-0 px-5 flex-shrink-0"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <TabButton
          label="Questions"
          icon={<FileQuestion className="w-4 h-4" />}
          active={activeTab === 'questions'}
          disabled={!hasSelection}
          onClick={() => onTabChange('questions')}
        />
        <TabButton
          label="Configuration"
          icon={<Settings className="w-4 h-4" />}
          active={activeTab === 'config'}
          disabled={!hasSelection}
          onClick={() => onTabChange('config')}
        />
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {!hasSelection || !stageId || !selectedStage ? (
          <EmptyInspectorState />
        ) : activeTab === 'questions' ? (
          <QuestionsMainPane jobId={jobId} stageId={stageId} />
        ) : (
          <StageConfigurationTab
            stage={selectedStage}
            onChange={onStageChange}
          />
        )}
      </div>
    </div>
  )
}

type TabButtonProps = {
  label: string
  icon: React.ReactNode
  active: boolean
  disabled?: boolean
  onClick: () => void
}

function TabButton({
  label,
  icon,
  active,
  disabled,
  onClick,
}: TabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition ${
        active
          ? 'text-blue-700 border-blue-600'
          : disabled
            ? 'text-zinc-300 border-transparent cursor-not-allowed' // TODO(design-review): no px-token equivalent for text-zinc-300 disabled state
            : 'text-zinc-500 border-transparent hover:text-zinc-900 hover:border-zinc-300' // TODO(design-review): hover pseudo-states + text-zinc-900/border-zinc-300 have no px-token equivalent; text-zinc-500 kept for consistency with conditional branch
      }`}
    >
      {icon}
      {label}
    </button>
  )
}
