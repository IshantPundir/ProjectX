'use client'

import { BrandLogo } from '@/components/px'
import type { ReportRead, RiskBand } from '@/lib/api/reports'
import { TONE_BG, TONE_INK, verdictMeta } from '../report-format'
import { GlassBackdrop } from './GlassBackdrop'

// Minimal top chrome: a faint product watermark on the left, and a small glass
// pill on the right with the verdict + close. Scores moved to the left rail.
export function TheaterTopBar({
  report,
  riskBand,
  onClose,
  showClose = true,
}: {
  report: ReportRead
  riskBand: RiskBand | null
  onClose: () => void
  // The public recordings page hides the close ✕ — there is nowhere to close to.
  showClose?: boolean
}) {
  const v = verdictMeta(report.verdict)
  return (
    <div className="pointer-events-none flex items-start justify-between">
      <BrandLogo height={18} className="theater-watermark" />
      <div className="theater-glass pointer-events-auto flex items-center gap-2 rounded-full px-2.5 py-1.5">
        <GlassBackdrop />
        {riskBand === 'high' && (
          <span className="whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-bold"
            style={{ background: TONE_BG.danger, color: TONE_INK.danger }}>
            ⚠ Integrity risk
          </span>
        )}
        <span className="whitespace-nowrap rounded-full px-2.5 py-0.5 text-[10.5px] font-bold"
          style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}>
          {v.label}
        </span>
        {showClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-6 w-6 flex-none place-items-center rounded-full border text-[12px]"
            style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}
          >
            ✕
          </button>
        )}
      </div>
    </div>
  )
}
