import type { SignalAssessmentOut } from '@/lib/api/reports'

export function SignalAuditTable({ assessments }: { assessments: SignalAssessmentOut[] }) {
  if (!assessments.length) return null
  return (
    <details className="rounded-xl border bg-white p-4" style={{ borderColor: 'var(--px-hairline)' }}>
      <summary className="cursor-pointer text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
        Audit detail — signal by signal ({assessments.length})
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr style={{ color: 'var(--px-fg-4)' }} className="text-left">
              <th className="py-1 pr-2 font-semibold">Signal</th>
              <th className="py-1 pr-2 font-semibold">Must-have</th>
              <th className="py-1 pr-2 font-semibold">Engine → Final</th>
              <th className="py-1 pr-2 font-semibold">Grade</th>
              <th className="py-1 font-semibold">Note</th>
            </tr>
          </thead>
          <tbody>
            {assessments.map((a) => (
              <tr key={a.signal} className="border-t align-top" style={{ borderColor: 'var(--px-hairline)' }}>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-2)' }}>{a.signal}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>{a.knockout ? 'yes' : '—'}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  {a.engine_state} → {a.final_state}{a.overridden ? ' *' : ''}
                </td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>{a.grade ?? '—'}</td>
                <td className="py-1" style={{ color: 'var(--px-fg-4)' }}>{a.override_reason ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>* re-checked and adjusted by the post-session scorer.</p>
      </div>
    </details>
  )
}
