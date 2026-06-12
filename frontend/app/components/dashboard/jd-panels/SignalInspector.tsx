'use client'

import { useState } from 'react'

import { DangerConfirmDialog } from '@/components/px'
import type { SignalItem } from '@/lib/api/jobs'

import { Confidence } from './components/Confidence'
import { InspectorAction } from './components/InspectorAction'
import { SnippetHighlighted } from './components/SnippetHighlighted'
import { SourceBadge } from './components/SourceBadge'
import { findSnippet } from './helpers/findSnippet'
import { suggestQuestions } from './helpers/suggestQuestions'
import { weightToConfidence } from './helpers/weightToConfidence'

export function SignalInspector({
  signal,
  signalIndex,
  jobRaw,
  canManage,
  onUpdate,
  onRemove,
}: {
  signal: SignalItem
  signalIndex: number
  jobRaw: string
  canManage: boolean
  onUpdate: (patch: Partial<SignalItem>) => void
  onRemove: () => void
}) {
  const [confirmingRemove, setConfirmingRemove] = useState(false)

  const confidence = weightToConfidence(signal.weight)
  const confidenceLabel =
    confidence >= 0.75
      ? 'Looking solid'
      : confidence >= 0.5
        ? 'Worth a second look'
        : "Copilot wasn't sure"
  const confidenceColor =
    confidence >= 0.75
      ? 'var(--px-ok)'
      : confidence >= 0.5
        ? 'var(--px-caution)'
        : 'var(--px-danger)'

  // Best-effort snippet: search the raw JD for the signal value to show
  // "where this came from" — the design treats this as the hero receipt.
  const snippet = findSnippet(jobRaw, signal.value)

  const draftedQuestions: string[] = suggestQuestions(signal)

  return (
    <>
      <aside
        className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border"
        style={{
          // 48px AppShell top bar + 12px gap = 60
          top: 60,
          maxHeight: 'calc(100vh - 72px)',
          background: 'var(--px-bg-2)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="border-b px-4 py-4"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <div className="mb-1.5 flex items-center gap-2">
            <SourceBadge kind={signal.source} />
            <span className="px-eyebrow">Signal #{signalIndex + 1}</span>
          </div>
          <div
            className="mb-0.5 text-[16px] font-semibold"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.2px' }}
          >
            {signal.value}
          </div>
          <div className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            {signal.priority === 'required' ? 'Must-have' : 'Nice-to-have'} ·{' '}
            {signal.source === 'ai_extracted'
              ? 'Copilot pulled this verbatim'
              : signal.source === 'ai_inferred'
                ? 'Copilot inferred this from context'
                : 'You added this'}
            {signal.knockout && ' · deal-breaker'}
          </div>
          <div className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-3)' }}>
            <span className="font-medium" style={{ color: 'var(--px-fg-2)' }}>
              {(signal.purpose ?? 'skill') === 'eligibility' ? 'Eligibility' : 'Skill'}
            </span>
            <span>
              {' '}
              &middot;{' '}
              {(signal.purpose ?? 'skill') === 'eligibility'
                ? 'recruiter pre-screened'
                : 'tested in the AI screen'}
            </span>
          </div>
        </div>

        <div
          className="border-b px-4 py-3.5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <div className="px-eyebrow mb-2.5">How confident</div>
          <div className="mb-2.5 flex items-center gap-3">
            <Confidence value={confidence} />
            <span
              className="text-[12px] font-medium"
              style={{ color: confidenceColor }}
            >
              {confidenceLabel}
            </span>
          </div>
          <div
            className="text-[12.5px]"
            style={{ color: 'var(--px-fg-3)', lineHeight: 1.55 }}
          >
            {signal.inference_basis
              ? signal.inference_basis
              : confidence >= 0.75
                ? 'The JD calls this out explicitly, and it aligns with every similar role on your team.'
                : 'The JD is ambiguous on this one — I made a judgment call based on the seniority and role context.'}
          </div>
        </div>

        <div
          className="border-b px-4 py-3.5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <div className="px-eyebrow mb-2.5">Where in the JD</div>
          {snippet ? (
            <div
              className="rounded-md border p-3 text-[12.5px]"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
                lineHeight: 1.55,
                color: 'var(--px-fg-2)',
                fontFamily: 'var(--font-serif)',
              }}
            >
              <SnippetHighlighted text={snippet} needle={signal.value} />
            </div>
          ) : (
            <div
              className="rounded-md border p-3 text-[12.5px] italic"
              style={{
                background: 'var(--px-surface-2)',
                borderColor: 'var(--px-hairline)',
                color: 'var(--px-fg-4)',
              }}
            >
              Not a direct match in the JD — Copilot inferred this from context.
            </div>
          )}
        </div>

        <div
          className="border-b px-4 py-3.5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <div className="px-eyebrow mb-2.5">Copilot drafted these questions</div>
          <div className="flex flex-col gap-[7px]">
            {draftedQuestions.map((q, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md border px-2.5 py-2 text-[12.5px]"
                style={{
                  background: 'var(--px-surface)',
                  borderColor: 'var(--px-hairline)',
                  color: 'var(--px-fg-2)',
                  lineHeight: 1.5,
                }}
              >
                <span
                  className="px-mono mt-0.5 text-[10.5px]"
                  style={{ color: 'var(--px-fg-4)' }}
                >
                  Q{i + 1}
                </span>
                <span className="flex-1">{q}</span>
              </div>
            ))}
          </div>
        </div>

        {canManage && (
          <div className="px-4 py-3.5">
            <div className="px-eyebrow mb-2.5">Actions</div>
            <div className="flex flex-col gap-1">
              <InspectorAction
                label="Approve as must-have"
                keys={['⌘', '↵'] as const}
                primary
                onClick={() =>
                  onUpdate({
                    priority: 'required',
                    weight: 3,
                    knockout: false,
                  })
                }
              />
              <InspectorAction
                label="Mark deal-breaker"
                keys={['⇧', 'K'] as const}
                onClick={() =>
                  onUpdate({
                    priority: 'required',
                    weight: 3,
                    knockout: true,
                  })
                }
              />
              <InspectorAction
                label="Move to nice-to-have"
                keys={['⇧', 'D'] as const}
                onClick={() =>
                  onUpdate({
                    priority: 'preferred',
                    knockout: false,
                  })
                }
              />
              <InspectorAction
                label="Remove signal"
                keys={['⌫'] as const}
                danger
                onClick={() => setConfirmingRemove(true)}
              />
            </div>
          </div>
        )}

        <div className="flex-1" />

        <div
          className="flex items-center gap-2 border-t px-4 py-2.5 text-[11px]"
          style={{
            background: 'var(--px-bg-2)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-4)',
          }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: 'var(--px-accent)' }}
            aria-hidden="true"
          />
          Copilot · review changes before publishing
        </div>
      </aside>

      <DangerConfirmDialog
        open={confirmingRemove}
        title="Remove signal"
        description={
          <>
            Remove signal <strong>{signal.value}</strong>?
          </>
        }
        confirmLabel="Remove signal"
        pendingLabel="Removing…"
        pending={false}
        onConfirm={() => {
          onRemove()
          setConfirmingRemove(false)
        }}
        onClose={() => setConfirmingRemove(false)}
      />
    </>
  )
}
