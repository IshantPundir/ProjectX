import type { EvidenceOut } from '@/lib/api/reports'
import { formatTimestamp } from './report-format'

interface EvidenceQuoteProps {
  evidence: EvidenceOut
  /** Tone of the left rule + chip (defaults to accent violet). */
  toneVar?: string
}

/**
 * One grounded evidence quote. The timestamp chip is the forward-compat
 * seek control for sub-project B (recording): it already carries
 * `timestamp_ms` + `question_id`; today it is inert (`data-seek-stub`),
 * later it becomes a button that seeks the session player. Keep the prop
 * shape stable so that is a drop-in.
 */
export function EvidenceQuote({ evidence, toneVar = 'var(--px-accent)' }: EvidenceQuoteProps) {
  return (
    <div
      className="mt-2 rounded-r-lg py-1.5 pl-2.5 pr-2"
      style={{ borderLeft: `3px solid ${toneVar}`, background: 'var(--px-accent-tint)' }}
    >
      <p className="text-[11.5px] italic" style={{ color: 'var(--px-fg)' }}>
        &ldquo;{evidence.quote}&rdquo;
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[9.5px]" style={{ color: 'var(--px-fg-3)' }}>
        <span
          data-seek-stub
          title="Jump to this moment — playback arrives with session recording"
          className="inline-flex items-center gap-1 rounded border px-1.5 py-px font-semibold"
          style={{ borderColor: 'var(--px-hairline-strong)', background: 'var(--px-surface)', color: toneVar }}
        >
          &#9654;&nbsp;<span>{formatTimestamp(evidence.timestamp_ms)}</span>
        </span>
        <span style={{ fontFamily: 'var(--font-mono, monospace)' }}>Q {evidence.question_id}</span>
        {evidence.grounded ? (
          <span aria-label="verified in transcript">grounded &#10003;</span>
        ) : (
          <span aria-label="unverified — quote not found in transcript" style={{ color: 'var(--px-caution)' }}>
            &#9888; unverified
          </span>
        )}
      </div>
    </div>
  )
}
