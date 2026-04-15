'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type {
  SignalItem,
  SignalType,
  SignalStage,
  SignalPriority,
} from '@/lib/api/jobs'
import { useJobEditStore } from '@/stores/job-edit'

const SENIORITY_OPTIONS = [
  { value: 'junior', label: 'Junior' },
  { value: 'mid', label: 'Mid' },
  { value: 'senior', label: 'Senior' },
  { value: 'lead', label: 'Lead' },
  { value: 'principal', label: 'Principal' },
] as const

const TYPE_LABELS: Record<SignalType, string> = {
  credential: 'Credentials',
  experience: 'Experience',
  competency: 'Competencies',
  behavioral: 'Behavioral',
}

const SCREEN_TYPE_ORDER: SignalType[] = ['credential', 'experience', 'competency', 'behavioral']
const INTERVIEW_TYPE_ORDER: SignalType[] = ['competency', 'behavioral', 'credential', 'experience']

const TYPE_OPTIONS: { value: SignalType; label: string }[] = [
  { value: 'competency', label: 'Competency' },
  { value: 'experience', label: 'Experience' },
  { value: 'credential', label: 'Credential' },
  { value: 'behavioral', label: 'Behavioral' },
]

const STAGE_OPTIONS: { value: SignalStage; label: string }[] = [
  { value: 'screen', label: 'Screen' },
  { value: 'interview', label: 'Interview' },
]

const WEIGHT_OPTIONS: { value: string; label: string }[] = [
  { value: '1', label: '1' },
  { value: '2', label: '2' },
  { value: '3', label: '3' },
]

const PRIORITY_OPTIONS: { value: SignalPriority; label: string }[] = [
  { value: 'required', label: 'Required' },
  { value: 'preferred', label: 'Preferred' },
]

function chipStyles(item: SignalItem): string {
  const base =
    'inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border font-medium'
  if (item.source === 'ai_extracted') {
    return `${base} bg-blue-50 text-blue-700 border-blue-200`
  }
  if (item.source === 'ai_inferred') {
    return `${base} bg-amber-50 text-amber-800 border border-dashed border-amber-400`
  }
  return `${base} bg-emerald-50 text-emerald-700 border-emerald-200`
}

function dotColor(source: SignalItem['source']): string {
  if (source === 'ai_extracted') return 'bg-blue-500'
  if (source === 'ai_inferred') return 'bg-amber-500'
  return 'bg-emerald-500'
}

/**
 * Editable chip row: shows the chip value with delete, weight, knockout, type, and stage controls.
 * `realIndex` is the index into the flat draft.signals array.
 */
function EditableChipRow({
  item,
  realIndex,
}: {
  item: SignalItem
  realIndex: number
}) {
  const removeChip = useJobEditStore((s) => s.removeChip)
  const updateSignal = useJobEditStore((s) => s.updateSignal)

  return (
    <div className="flex flex-wrap items-center gap-1.5 py-1">
      {/* Chip label with provenance color */}
      <span className={chipStyles(item)}>
        <span className={`w-1.5 h-1.5 rounded-full ${dotColor(item.source)}`} />
        {item.value}
        {item.knockout && (
          <span className="ml-0.5 px-1 py-px text-[9px] font-bold leading-none rounded bg-red-100 text-red-600 border border-red-200">
            KO
          </span>
        )}
        <button
          type="button"
          onClick={() => removeChip(realIndex)}
          className="ml-0.5 text-current opacity-60 hover:opacity-100 transition-opacity"
          aria-label={`Remove ${item.value}`}
        >
          &times;
        </button>
      </span>

      {/* Inline controls */}
      <div className="flex items-center gap-1">
        {/* Weight selector */}
        <Select
          value={String(item.weight)}
          onValueChange={(v) =>
            updateSignal(realIndex, { weight: Number(v) as 1 | 2 | 3 })
          }
        >
          <SelectTrigger className="h-5 w-12 text-[10px] px-1" aria-label="Weight">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WEIGHT_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                W{opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Knockout toggle */}
        <button
          type="button"
          onClick={() => updateSignal(realIndex, { knockout: !item.knockout })}
          className={`h-5 px-1.5 text-[10px] font-medium rounded border transition-colors ${
            item.knockout
              ? 'bg-red-100 text-red-700 border-red-300'
              : 'bg-zinc-50 text-zinc-400 border-zinc-200 hover:bg-zinc-100'
          }`}
          aria-label={item.knockout ? 'Remove knockout' : 'Mark as knockout'}
        >
          KO
        </button>

        {/* Type dropdown */}
        <Select
          value={item.type}
          onValueChange={(v) => updateSignal(realIndex, { type: v as SignalType })}
        >
          <SelectTrigger className="h-5 w-[72px] text-[10px] px-1" aria-label="Type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TYPE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Stage dropdown */}
        <Select
          value={item.stage}
          onValueChange={(v) => updateSignal(realIndex, { stage: v as SignalStage })}
        >
          <SelectTrigger className="h-5 w-[68px] text-[10px] px-1" aria-label="Stage">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STAGE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Priority dropdown */}
        <Select
          value={item.priority}
          onValueChange={(v) => updateSignal(realIndex, { priority: v as SignalPriority })}
        >
          <SelectTrigger className="h-5 w-[72px] text-[10px] px-1" aria-label="Priority">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRIORITY_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}

/**
 * Add-signal input that appears at the bottom of each type section.
 * Pre-fills type, stage, and priority so the user only types the value.
 */
function AddSignalInput({
  type,
  stage,
  priority,
}: {
  type: SignalType
  stage: SignalStage
  priority: SignalPriority
}) {
  const [inputValue, setInputValue] = useState('')
  const addChip = useJobEditStore((s) => s.addChip)

  function handleAdd() {
    const trimmed = inputValue.trim()
    if (!trimmed) return
    addChip(trimmed, type, stage, priority)
    setInputValue('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAdd()
    }
  }

  return (
    <div className="flex gap-1.5 mt-1.5">
      <Input
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Add signal..."
        className="flex-1 text-xs h-6"
      />
      <Button
        type="button"
        variant="outline"
        size="xs"
        onClick={handleAdd}
        disabled={!inputValue.trim()}
      >
        Add
      </Button>
    </div>
  )
}

/**
 * Renders a type group within a stage section (edit mode).
 * Shows editable chips + add input with pre-filled type/stage.
 */
function TypeGroupEditor({
  label,
  items,
  type,
  stage,
}: {
  label: string
  items: { item: SignalItem; realIndex: number }[]
  type: SignalType
  stage: SignalStage
}) {
  return (
    <div>
      <h5 className="text-[10px] font-medium uppercase tracking-wide text-zinc-400 mb-1">
        {label}
      </h5>
      {items.map(({ item, realIndex }) => (
        <EditableChipRow
          key={`${realIndex}-${item.value}`}
          item={item}
          realIndex={realIndex}
        />
      ))}
      <AddSignalInput type={type} stage={stage} priority="required" />
    </div>
  )
}

/**
 * Renders a stage section (screen or interview) in edit mode.
 */
function EditableStageSection({
  label,
  signals,
  typeOrder,
  stage,
}: {
  label: string
  signals: { item: SignalItem; realIndex: number }[]
  typeOrder: SignalType[]
  stage: SignalStage
}) {
  // Group by type, preserving realIndex
  const byType = new Map<SignalType, { item: SignalItem; realIndex: number }[]>()
  for (const entry of signals) {
    const list = byType.get(entry.item.type) ?? []
    list.push(entry)
    byType.set(entry.item.type, list)
  }

  // Sort competencies by weight descending
  const competencies = byType.get('competency')
  if (competencies) {
    competencies.sort((a, b) => b.item.weight - a.item.weight)
  }

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {label}
      </h4>
      {typeOrder.map((type) => {
        const items = byType.get(type) ?? []
        return (
          <TypeGroupEditor
            key={type}
            label={TYPE_LABELS[type]}
            items={items}
            type={type}
            stage={stage}
          />
        )
      })}
    </div>
  )
}

export function EditableSignalsPanel() {
  const draft = useJobEditStore((s) => s.draft)
  const updateDraft = useJobEditStore((s) => s.updateDraft)

  if (!draft) return null

  // Build indexed entries for grouping
  const indexedSignals = draft.signals.map((item, realIndex) => ({ item, realIndex }))
  const screenSignals = indexedSignals.filter((e) => e.item.stage === 'screen')
  const interviewSignals = indexedSignals.filter((e) => e.item.stage === 'interview')

  return (
    <div className="space-y-5">
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
          Role Summary
        </h4>
        <Textarea
          value={draft.role_summary}
          onChange={(e) => updateDraft({ role_summary: e.target.value })}
          rows={3}
          className="text-xs"
        />
      </div>

      <div>
        <div className="text-zinc-400 uppercase tracking-wide text-xs mb-1">
          Seniority
        </div>
        <Select
          value={draft.seniority_level}
          onValueChange={(v) =>
            updateDraft({
              seniority_level: v as typeof draft.seniority_level,
            })
          }
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue placeholder="Select level" />
          </SelectTrigger>
          <SelectContent>
            {SENIORITY_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <EditableStageSection
        label="Phone Screen"
        signals={screenSignals}
        typeOrder={SCREEN_TYPE_ORDER}
        stage="screen"
      />
      <EditableStageSection
        label="Deep Interview"
        signals={interviewSignals}
        typeOrder={INTERVIEW_TYPE_ORDER}
        stage="interview"
      />
    </div>
  )
}
