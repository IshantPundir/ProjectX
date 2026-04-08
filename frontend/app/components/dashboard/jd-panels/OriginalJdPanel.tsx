'use client'

import { useState } from 'react'

type Props = {
  descriptionRaw: string
  projectScopeRaw?: string | null
}

/**
 * Collapses to a vertical drawer rail below the 3xl breakpoint (1440px).
 * Above 3xl: full column. Below 3xl: thin vertical rail with a
 * "View raw JD" label; clicking expands into an overlay modal.
 */
export function OriginalJdPanel({ descriptionRaw, projectScopeRaw }: Props) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      {/* Full column — visible at 3xl and above */}
      <aside className="hidden 3xl:flex 3xl:col-span-1 flex-col bg-white rounded-lg border border-zinc-200 p-5 overflow-auto">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-3 pb-2 border-b border-zinc-100">
          Original JD
        </h3>
        <pre className="whitespace-pre-wrap text-xs text-zinc-700 font-mono leading-relaxed">
          {descriptionRaw}
        </pre>
        {projectScopeRaw && (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mt-6 mb-3 pb-2 border-b border-zinc-100">
              Project Scope
            </h3>
            <pre className="whitespace-pre-wrap text-xs text-zinc-700 font-mono leading-relaxed">
              {projectScopeRaw}
            </pre>
          </>
        )}
      </aside>

      {/* Vertical rail — visible below 3xl */}
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="3xl:hidden w-8 flex items-center justify-center bg-white border border-zinc-200 rounded-lg hover:bg-zinc-50"
        aria-label="View raw JD"
      >
        <span
          className="text-xs text-zinc-500 font-medium whitespace-nowrap"
          style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
        >
          View raw JD
        </span>
      </button>

      {/* Expanded overlay */}
      {expanded && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8"
          onClick={() => setExpanded(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Original JD"
        >
          <div
            className="bg-white rounded-lg max-w-3xl max-h-[80vh] overflow-auto p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold">Original JD</h3>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <pre className="whitespace-pre-wrap text-sm text-zinc-700 font-mono">
              {descriptionRaw}
            </pre>
            {projectScopeRaw && (
              <>
                <h3 className="text-sm font-semibold mt-6 mb-2">Project Scope</h3>
                <pre className="whitespace-pre-wrap text-sm text-zinc-700 font-mono">
                  {projectScopeRaw}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
