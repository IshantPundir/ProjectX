'use client'

import * as React from 'react'
import Link from 'next/link'
import { Share2 } from 'lucide-react'

import { Button } from '@/components/px'
import type { Verdict } from '@/lib/api/reports'
import { ShareReportDialog } from './ShareReportDialog'
import { VerdictChip } from './VerdictBand'

interface Props {
  sessionId: string
  candidateName: string
  candidateId: string
  title: string
  subtitle: string
  verdict: Verdict
  canRegenerate: boolean
  onRegenerate: () => void
}

export function ReportTopBar({ sessionId, candidateName, candidateId, title, subtitle, verdict, canRegenerate, onRegenerate }: Props) {
  const [shareOpen, setShareOpen] = React.useState(false)
  return (
    <div className="mb-4 flex items-center gap-3">
      <Link href={`/candidates/${candidateId}?tab=sessions`} className="text-[12px] hover:underline" style={{ color: 'var(--px-fg-3)' }}>
        ← {candidateName}
      </Link>
      <div className="min-w-0 flex-1">
        <h1 className="px-serif m-0 truncate text-[24px] font-normal" style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}>
          Evaluation — {title}
        </h1>
        <p className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>{subtitle}</p>
      </div>
      <VerdictChip verdict={verdict} />
      <Button type="button" variant="outline" size="sm" onClick={() => setShareOpen(true)}>
        <Share2 size={14} className="mr-1.5" /> Share
      </Button>
      {canRegenerate && (
        <Button type="button" variant="outline" size="sm" onClick={onRegenerate}>Regenerate</Button>
      )}
      <ShareReportDialog sessionId={sessionId} open={shareOpen} onOpenChange={setShareOpen} />
    </div>
  )
}
