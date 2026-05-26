'use client'

import type { ReactNode } from 'react'
import { Button } from '@/components/px'

function Centered({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-[640px] px-8 py-16 text-center">{children}</div>
}

export function ReportEmptyState({ canGenerate, onGenerate }: { canGenerate: boolean; onGenerate: () => void }) {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>No evaluation yet</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        This session has no report. A report is generated after an AI-screening session completes.
      </p>
      {canGenerate && (
        <Button type="button" variant="primary" size="sm" className="mt-5" onClick={onGenerate}>Generate report</Button>
      )}
    </Centered>
  )
}

export function ReportPendingState() {
  return (
    <Centered>
      <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2" style={{ borderColor: 'var(--px-accent)', borderTopColor: 'transparent' }} />
      <h2 className="px-serif mt-4 text-2xl" style={{ color: 'var(--px-fg)' }}>Scoring this interview…</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        The evaluation is being generated. This page updates automatically.
      </p>
    </Centered>
  )
}

export function ReportFailedState({ canRegenerate, onRegenerate }: { canRegenerate: boolean; onRegenerate: () => void }) {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-danger)' }}>Report generation failed</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Something went wrong while scoring this interview.
      </p>
      {canRegenerate && (
        <Button type="button" variant="primary" size="sm" className="mt-5" onClick={onRegenerate}>Regenerate</Button>
      )}
    </Centered>
  )
}

export function ReportForbiddenState() {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>Access denied</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        You don&rsquo;t have permission to view this report.
      </p>
    </Centered>
  )
}
