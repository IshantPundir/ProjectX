'use client'

import { useEffect, useState } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { toast } from 'sonner'

import type { KanbanColumn } from '@/lib/api/candidates'
import type { StageType } from '@/lib/api/pipelines'
import { useSetStageOtp } from '@/lib/hooks/use-set-stage-otp'

import CandidateKanbanCard from './CandidateKanbanCard'
import {
  readAutoInviteEnabled,
  writeAutoInviteEnabled,
} from './auto-invite-storage'

interface Props {
  stage: KanbanColumn
  jobId: string
  /** From the pipeline lookup in the parent. Undefined while the
   *  pipeline is still loading or if the stage doesn't have a matching
   *  pipeline-stage row (shouldn't happen in practice). */
  stageType: StageType | undefined
  /** Stage's persisted otp_required_default (from the pipeline lookup in the
   *  parent). Undefined while the pipeline is still loading. */
  otpRequired?: boolean
}

export default function CandidateKanbanColumn({
  stage,
  jobId,
  stageType,
  otpRequired,
}: Props) {
  const { setNodeRef, isOver } = useDroppable({
    id: stage.stage_id,
    data: { stageId: stage.stage_id },
  })

  return (
    <div
      ref={setNodeRef}
      className="flex h-full w-80 flex-shrink-0 flex-col rounded-lg border transition-colors"
      style={{
        background: 'var(--px-bg-2)',
        borderColor: isOver ? 'var(--px-accent)' : 'var(--px-hairline)',
        boxShadow: isOver ? '0 0 0 1px var(--px-accent-line)' : undefined,
        minHeight: 0,
      }}
    >
      <header
        className="flex items-center gap-2 px-3"
        style={{ padding: '10px 12px 8px' }}
      >
        <h3
          className="m-0 truncate text-[11.5px] font-semibold uppercase"
          style={{ letterSpacing: '0.2px', color: 'var(--px-fg-2)' }}
        >
          {stage.stage_name}
        </h3>
        <span
          className="px-mono text-[11px]"
          style={{
            color: 'var(--px-fg-4)',
            fontVariantNumeric: 'tabular-nums',
          }}
          aria-label={`${stage.candidates.length} candidates in ${stage.stage_name}`}
        >
          {stage.candidates.length}
        </span>
        {stageType === 'ai_screening' && (
          <AutoInviteToggle jobId={jobId} stageId={stage.stage_id} />
        )}
        {stageType === 'ai_screening' && (
          <OtpRequiredToggle
            jobId={jobId}
            stageId={stage.stage_id}
            initial={otpRequired ?? false}
          />
        )}
      </header>

      <div
        className="flex min-h-[4rem] flex-1 flex-col gap-1.5 overflow-y-auto"
        style={{ padding: '2px 8px 8px' }}
      >
        {stage.candidates.length === 0 ? (
          <p
            className="py-6 text-center text-[11px]"
            style={{ color: 'var(--px-fg-5)' }}
          >
            Drop candidates here
          </p>
        ) : (
          stage.candidates.map((card) => (
            <CandidateKanbanCard key={card.assignment_id} card={card} />
          ))
        )}
      </div>
    </div>
  )
}

/**
 * Inline column-header checkbox that toggles the auto-invite preference
 * for this (job, stage) pair. SSR-safe: initial state is the default
 * (enabled), then `useEffect` reads localStorage post-mount so server
 * markup and client first paint match.
 */
function AutoInviteToggle({
  jobId,
  stageId,
}: {
  jobId: string
  stageId: string
}) {
  const [enabled, setEnabled] = useState(true)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEnabled(readAutoInviteEnabled(jobId, stageId))
  }, [jobId, stageId])

  function handleChange(next: boolean) {
    setEnabled(next)
    writeAutoInviteEnabled(jobId, stageId, next)
  }

  return (
    <label
      className="ml-auto inline-flex cursor-pointer items-center gap-1.5"
      style={{ color: 'var(--px-fg-3)' }}
      title="When enabled, candidates dropped into this stage are auto-emailed an invite link (OTP requirement follows the OTP toggle)."
    >
      <input
        type="checkbox"
        checked={enabled}
        onChange={(e) => handleChange(e.target.checked)}
        aria-label="Auto-send invite on stage entry"
        className="cursor-pointer"
        style={{ width: 12, height: 12, accentColor: 'var(--px-accent)' }}
      />
      <span className="text-[10px] font-medium uppercase" style={{ letterSpacing: '0.4px' }}>
        Auto-invite
      </span>
    </label>
  )
}

/**
 * Column-header checkbox that persists this stage's OTP requirement to the
 * backend (job_pipeline_stages.otp_required_default). Unlike Auto-invite
 * (browser-local), OTP is a security control, so it is server-persisted and
 * shared across recruiters. Optimistic: flips immediately, reverts + toasts on
 * error. `initial` comes from pipeline data; we re-sync when it changes (the
 * mutation writes the fresh instance into the cache on success).
 */
export function OtpRequiredToggle({
  jobId,
  stageId,
  initial,
}: {
  jobId: string
  stageId: string
  initial: boolean
}) {
  const [enabled, setEnabled] = useState(initial)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEnabled(initial)
  }, [initial])

  const setOtp = useSetStageOtp(jobId)

  function handleChange(next: boolean) {
    setEnabled(next) // optimistic
    setOtp.mutate(
      { stageId, otpRequired: next },
      {
        onError: (err) => {
          setEnabled(!next) // revert
          toast.error(err.message || 'Failed to update OTP setting')
        },
      },
    )
  }

  return (
    <label
      className="inline-flex cursor-pointer items-center gap-1.5"
      style={{ color: 'var(--px-fg-3)' }}
      title="When enabled, candidates must verify a one-time code before the interview starts."
    >
      <input
        type="checkbox"
        checked={enabled}
        disabled={setOtp.isPending}
        onChange={(e) => handleChange(e.target.checked)}
        aria-label="Require OTP verification for this stage"
        className="cursor-pointer"
        style={{ width: 12, height: 12, accentColor: 'var(--px-accent)' }}
      />
      <span className="text-[10px] font-medium uppercase" style={{ letterSpacing: '0.4px' }}>
        OTP
      </span>
    </label>
  )
}
