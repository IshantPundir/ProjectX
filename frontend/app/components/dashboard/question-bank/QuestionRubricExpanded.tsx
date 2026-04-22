'use client'

import type { QuestionResponse } from '@/lib/api/question-banks'

type Props = { question: QuestionResponse }

/**
 * Expanded question detail — aligned to the v4 QuestionBank design.
 *
 * Backend stores rubric keys as `excellent / meets_bar / below_bar`. The
 * design uses `Exceeds / Meets / Below`. We map at render time; no data
 * change required.
 */
export function QuestionRubricExpanded({ question }: Props) {
  return (
    <div className="space-y-5">
      {/* Listen for + Red flags side-by-side */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div
            className="mb-2 text-[10.5px] font-semibold uppercase"
            style={{ letterSpacing: '1.1px', color: 'var(--px-ok)' }}
          >
            Listen for
          </div>
          <ul className="space-y-1">
            {question.positive_evidence.map((item, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-[12px]"
                style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
              >
                <span
                  className="mt-[3px] inline-block h-[5px] w-[5px] flex-shrink-0 rounded-full"
                  style={{ background: 'var(--px-ok)' }}
                  aria-hidden="true"
                />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div
            className="mb-2 text-[10.5px] font-semibold uppercase"
            style={{ letterSpacing: '1.1px', color: 'var(--px-danger)' }}
          >
            Red flags
          </div>
          <ul className="space-y-1">
            {question.red_flags.map((item, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-[12px]"
                style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
              >
                <span
                  className="mt-[3px] inline-block h-[5px] w-[5px] flex-shrink-0 rounded-full"
                  style={{ background: 'var(--px-danger)' }}
                  aria-hidden="true"
                />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Follow-up probes */}
      {question.follow_ups.length > 0 && (
        <div>
          <div
            className="mb-2 text-[10.5px] font-semibold uppercase"
            style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
          >
            Follow-up probes
          </div>
          <ul className="space-y-1">
            {question.follow_ups.map((item, i) => (
              <li
                key={i}
                className="text-[12px]"
                style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
              >
                <span
                  className="mr-1.5"
                  style={{ color: 'var(--px-accent)' }}
                >
                  →
                </span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Rubric — 3-tier panel matching design's Exceeds / Meets / Below */}
      <div>
        <div
          className="mb-2 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Rubric
        </div>
        <div className="space-y-2">
          <RubricTier
            label="Exceeds"
            body={question.rubric.excellent}
            tone="ok"
          />
          <RubricTier
            label="Meets"
            body={question.rubric.meets_bar}
            tone="caution"
          />
          <RubricTier
            label="Below"
            body={question.rubric.below_bar}
            tone="danger"
          />
        </div>
      </div>
    </div>
  )
}

function RubricTier({
  label,
  body,
  tone,
}: {
  label: string
  body: string
  tone: 'ok' | 'caution' | 'danger'
}) {
  const toneMap = {
    ok: {
      bg: 'var(--px-ok-bg)',
      bd: 'var(--px-ok-line)',
      fg: 'var(--px-ok)',
    },
    caution: {
      bg: 'var(--px-caution-bg)',
      bd: 'var(--px-caution-line)',
      fg: 'var(--px-caution)',
    },
    danger: {
      bg: 'var(--px-danger-bg)',
      bd: 'var(--px-danger-line)',
      fg: 'var(--px-danger)',
    },
  }[tone]
  return (
    <div
      className="grid items-baseline gap-3 rounded-md border px-3 py-2 text-[12px]"
      style={{
        gridTemplateColumns: '70px 1fr',
        background: toneMap.bg,
        borderColor: toneMap.bd,
      }}
    >
      <span
        className="text-[10px] font-bold uppercase"
        style={{ letterSpacing: '0.6px', color: toneMap.fg }}
      >
        {label}
      </span>
      <span style={{ color: 'var(--px-fg-2)', lineHeight: 1.55 }}>
        {body}
      </span>
    </div>
  )
}
