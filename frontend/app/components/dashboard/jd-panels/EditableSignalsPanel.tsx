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
import type { SignalItem } from '@/lib/api/jobs'
import { useJobEditStore, type ChipSection } from '@/stores/job-edit'

const CHIP_SECTIONS: { key: ChipSection; label: string }[] = [
  { key: 'required_skills', label: 'Required Skills' },
  { key: 'preferred_skills', label: 'Preferred Skills' },
  { key: 'must_haves', label: 'Must Haves' },
  { key: 'good_to_haves', label: 'Good to Haves' },
]

const SENIORITY_OPTIONS = [
  { value: 'junior', label: 'Junior' },
  { value: 'mid', label: 'Mid' },
  { value: 'senior', label: 'Senior' },
  { value: 'lead', label: 'Lead' },
  { value: 'principal', label: 'Principal' },
] as const

function chipStyles(item: SignalItem): string {
  const base =
    'inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border font-medium'
  if (item.source === 'ai_extracted') {
    return `${base} bg-blue-50 text-blue-700 border-blue-200`
  }
  if (item.source === 'ai_inferred') {
    return `${base} bg-amber-50 text-amber-800 border border-dashed border-amber-400`
  }
  // recruiter
  return `${base} bg-emerald-50 text-emerald-700 border-emerald-200`
}

function dotColor(source: SignalItem['source']): string {
  if (source === 'ai_extracted') return 'bg-blue-500'
  if (source === 'ai_inferred') return 'bg-amber-500'
  return 'bg-emerald-500'
}

function ChipSectionEditor({ section }: { section: ChipSection; label: string }) {
  const [inputValue, setInputValue] = useState('')
  const draft = useJobEditStore((s) => s.draft)
  const addChip = useJobEditStore((s) => s.addChip)
  const removeChip = useJobEditStore((s) => s.removeChip)

  if (!draft) return null

  const items = draft[section]

  function handleAdd() {
    const trimmed = inputValue.trim()
    if (!trimmed) return
    addChip(section, trimmed)
    setInputValue('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAdd()
    }
  }

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {items.map((item, i) => (
          <span
            key={`${section}-${i}-${item.value}`}
            className={chipStyles(item)}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${dotColor(item.source)}`} />
            {item.value}
            <button
              type="button"
              onClick={() => removeChip(section, i)}
              className="ml-0.5 text-current opacity-60 hover:opacity-100 transition-opacity"
              aria-label={`Remove ${item.value}`}
            >
              &times;
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1.5">
        <Input
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Add signal..."
          className="flex-1 text-xs h-7"
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
    </div>
  )
}

export function EditableSignalsPanel() {
  const draft = useJobEditStore((s) => s.draft)
  const updateDraft = useJobEditStore((s) => s.updateDraft)

  if (!draft) return null

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

      {CHIP_SECTIONS.map(({ key, label }) => (
        <div key={key}>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
            {label}
          </h4>
          <ChipSectionEditor section={key} label={label} />
        </div>
      ))}

      <div className="pt-3 border-t border-zinc-100 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-zinc-400 uppercase tracking-wide mb-1">
            Min Experience
          </div>
          <Input
            type="number"
            min={0}
            max={50}
            value={draft.min_experience_years}
            onChange={(e) =>
              updateDraft({ min_experience_years: Number(e.target.value) })
            }
            className="h-7 text-xs w-20"
          />
        </div>
        <div>
          <div className="text-zinc-400 uppercase tracking-wide mb-1">
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
      </div>
    </div>
  )
}
