'use client'

import { useEffect, useRef, useState } from 'react'

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
  const closeRef = useRef<HTMLButtonElement>(null)

  // Escape key closes the modal; focus is moved to the close button on open
  useEffect(() => {
    if (!expanded) return
    closeRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [expanded])

  return (
    <>
      {/* Full column — visible at 3xl and above */}
      <aside
        className="hidden flex-col overflow-auto rounded-[10px] border p-5 3xl:col-span-1 3xl:flex"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h3
          className="mb-3 border-b pb-2 text-[11px] font-semibold uppercase"
          style={{
            letterSpacing: '1.1px',
            color: 'var(--px-fg-4)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          Original JD
        </h3>
        <pre
          className="px-mono whitespace-pre-wrap text-[12px] leading-relaxed"
          style={{ color: 'var(--px-fg-2)' }}
        >
          {descriptionRaw}
        </pre>
        {projectScopeRaw && (
          <>
            <h3
              className="mb-3 mt-6 border-b pb-2 text-[11px] font-semibold uppercase"
              style={{
                letterSpacing: '1.1px',
                color: 'var(--px-fg-4)',
                borderColor: 'var(--px-hairline)',
              }}
            >
              Project scope
            </h3>
            <pre
              className="px-mono whitespace-pre-wrap text-[12px] leading-relaxed"
              style={{ color: 'var(--px-fg-2)' }}
            >
              {projectScopeRaw}
            </pre>
          </>
        )}
      </aside>

      {/* Vertical rail — visible below 3xl */}
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="flex w-8 items-center justify-center rounded-[10px] border 3xl:hidden"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
        aria-label="View raw JD"
      >
        <span
          className="whitespace-nowrap text-[11.5px] font-medium"
          style={{
            writingMode: 'vertical-rl',
            transform: 'rotate(180deg)',
            color: 'var(--px-fg-3)',
          }}
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
                ref={closeRef}
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
