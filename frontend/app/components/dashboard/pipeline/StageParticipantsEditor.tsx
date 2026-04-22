'use client'

import { useState } from 'react'
import { useAssignableUsers } from '@/lib/hooks/use-assignable-users'
import {
  participantSlotsFor,
  type ParticipantSlotSpec,
} from '@/lib/pipelines/categories'
import type {
  StageParticipantInput,
  StageParticipantResponse,
  StageType,
} from '@/lib/api/pipelines'

type Props = {
  jobId: string
  stage: {
    stage_type: StageType
    participants: StageParticipantResponse[]
  }
  onChange: (next: StageParticipantInput[]) => void
}

export function StageParticipantsEditor({ jobId, stage, onChange }: Props) {
  const slots = participantSlotsFor(stage.stage_type)
  if (slots.length === 0) return null

  return (
    <div className="space-y-4">
      {slots.map((slot) => (
        <ParticipantSlotSection
          key={slot.role}
          jobId={jobId}
          slot={slot}
          participants={stage.participants.filter((p) => p.role === slot.role)}
          onChange={(next) => {
            const others = stage.participants.filter((p) => p.role !== slot.role)
            onChange([
              ...others.map(({ user_id, role }) => ({ user_id, role })),
              ...next,
            ])
          }}
        />
      ))}
    </div>
  )
}

function ParticipantSlotSection({
  jobId,
  slot,
  participants,
  onChange,
}: {
  jobId: string
  slot: ParticipantSlotSpec
  participants: StageParticipantResponse[]
  onChange: (next: StageParticipantInput[]) => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const { data: pool, isLoading, isError } = useAssignableUsers(
    jobId,
    pickerOpen ? slot.role : null,
  )

  const assignedIds = new Set(participants.map((p) => p.user_id))

  const label =
    slot.role === 'interviewer' ? 'Interviewer' :
    slot.role === 'observer'    ? 'Observer' :
                                   'Reviewer'

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-xs font-medium text-zinc-700">
          {label}{slot.required ? '' : ' (optional)'}
        </div>
        <button
          type="button"
          onClick={() => setPickerOpen((v) => !v)}
          className="text-xs text-blue-600 hover:text-blue-800"
          aria-label={`Add ${label.toLowerCase()}`}
        >
          + Add
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {participants.length === 0 && (
          <div className="text-xs text-zinc-400">
            {slot.required ? 'None assigned yet.' : 'None assigned yet (optional).'}
          </div>
        )}
        {participants.map((p) => (
          <span
            key={p.user_id}
            className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs"
            title={p.email}
          >
            {p.full_name || p.email}
            <button
              type="button"
              aria-label={`Remove ${p.full_name || p.email}`}
              onClick={() =>
                onChange(
                  participants
                    .filter((x) => x.user_id !== p.user_id)
                    .map(({ user_id, role }) => ({ user_id, role })),
                )
              }
              className="text-zinc-400 hover:text-zinc-900"
            >
              ×
            </button>
          </span>
        ))}
      </div>

      {pickerOpen && (() => {
        const selectable = (pool ?? []).filter((u) => !assignedIds.has(u.user_id))
        const gateText =
          slot.role === 'interviewer' ? '"Interviewer" or "Hiring Manager"'
          : slot.role === 'observer' ? '"Observer", "Interviewer", "Hiring Manager", or "Recruiter"'
          : '"Hiring Manager"'
        return (
        <div className="mt-2 border border-zinc-200 rounded-md p-2 max-h-48 overflow-y-auto">
          {isLoading && <div className="text-xs text-zinc-400">Loading…</div>}
          {isError && (
            <div className="text-xs text-red-500">
              Couldn&apos;t load the team roster.
            </div>
          )}
          {!isLoading && !isError && selectable.length === 0 && (
            <div className="text-xs text-zinc-500 leading-relaxed">
              No eligible users found. The {label.toLowerCase()} slot accepts
              users with {gateText} role in this job&apos;s org unit (or an
              ancestor). Assign the role in Settings → Org Units first.
            </div>
          )}
          {selectable.map((u) => (
              <button
                type="button"
                key={u.user_id}
                onClick={() => {
                  onChange([
                    ...participants.map(({ user_id, role }) => ({ user_id, role })),
                    { user_id: u.user_id, role: slot.role },
                  ])
                  setPickerOpen(false)
                }}
                className="w-full text-left px-2 py-1 text-xs rounded hover:bg-zinc-50"
              >
                <div className="font-medium">{u.full_name || u.email}</div>
                <div className="text-zinc-400">{u.email}</div>
              </button>
            ))}
        </div>
        )
      })()}
    </div>
  )
}
