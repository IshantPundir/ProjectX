'use client'

type SourceKind = 'ai_extracted' | 'ai_inferred' | 'recruiter'

export function SourceBadge({ kind }: { kind: SourceKind }) {
  const map: Record<
    SourceKind,
    { label: string; cls: 'ai' | 'caution' | 'human'; tip: string }
  > = {
    ai_extracted: {
      label: 'From JD',
      cls: 'ai',
      tip: 'Pulled directly from the job description.',
    },
    ai_inferred: {
      label: 'Suggested',
      cls: 'caution',
      tip: "Copilot inferred this — worth a quick look.",
    },
    recruiter: {
      label: 'You added',
      cls: 'human',
      tip: 'You added this manually.',
    },
  }
  const m = map[kind]
  return (
    <span
      className={`px-chip ${m.cls}`}
      title={m.tip}
      style={{ height: 20, padding: '0 7px', fontSize: 10.5, fontWeight: 600, letterSpacing: 0.2 }}
    >
      {m.label}
    </span>
  )
}
