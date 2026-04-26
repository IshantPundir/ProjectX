'use client'

import type { ActivationPredicateFailure } from '@/lib/api/pipelines'

export function ActivationGate({
  failures,
  onActivate,
  onFocusStage,
}: {
  failures: ActivationPredicateFailure[]
  onActivate: () => void
  onFocusStage: (stageId: string) => void
}) {
  const ready = failures.length === 0
  const count = failures.length
  const headline = ready
    ? '✓ Ready to activate this job. Candidates will be able to enter the pipeline.'
    : `⚠ ${count} thing${count === 1 ? '' : 's'} needed before you can activate this job:`

  return (
    <div
      className={
        ready
          ? 'rounded-lg border border-emerald-300 bg-emerald-50 p-4'
          : 'rounded-lg border border-amber-300 bg-amber-50 p-4'
      }
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <p className="font-medium text-zinc-900">{headline}</p>
          {!ready && (
            <ul className="space-y-1 text-sm">
              {failures.map((f, i) => (
                <li key={`${f.code}-${f.stage_id ?? i}`}>
                  {f.stage_id ? (
                    <button
                      type="button"
                      onClick={() => onFocusStage(f.stage_id!)}
                      className="text-left text-zinc-700 underline-offset-2 hover:underline"
                    >
                      • {f.message}
                    </button>
                  ) : (
                    <span className="text-zinc-700">• {f.message}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
        <button
          type="button"
          disabled={!ready}
          onClick={onActivate}
          className="rounded bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          Activate
        </button>
      </div>
    </div>
  )
}
