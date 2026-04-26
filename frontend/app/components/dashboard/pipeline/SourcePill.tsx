'use client'

import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'

export interface SourcePillProps {
  sourceTemplateId: string | null
  sourceTemplateName: string | null
  sourceStarterKey: string | null
  diverged: boolean
  canSwap: boolean
  canUpdateSource: boolean
  onReset: () => void
  onSwap: () => void
  onSaveAsTemplate: () => void
  onUpdateSourceTemplate: () => void
}

export function SourcePill({
  sourceTemplateId,
  sourceTemplateName,
  sourceStarterKey,
  diverged,
  canSwap,
  canUpdateSource,
  onReset,
  onSwap,
  onSaveAsTemplate,
  onUpdateSourceTemplate,
}: SourcePillProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    function handleClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [menuOpen])

  // Determine pill label parts
  let sourceName: string
  let sourceType: string

  if (sourceTemplateId !== null && sourceTemplateName !== null) {
    sourceName = sourceTemplateName
    sourceType = 'team template'
  } else if (sourceStarterKey !== null) {
    sourceName = sourceStarterKey
    sourceType = 'system starter'
  } else {
    sourceName = 'Custom'
    sourceType = 'no source'
  }

  const canReset = sourceTemplateId !== null && canSwap

  return (
    <div className="flex items-center gap-2">
      {/* Source pill */}
      <span className="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 bg-zinc-50 px-3 py-1 text-sm text-zinc-700">
        {sourceTemplateId !== null || sourceStarterKey !== null ? (
          <>
            <span className="font-medium">{sourceName}</span>
            <span className="text-zinc-400">·</span>
            <span className="italic text-zinc-500">{sourceType}</span>
          </>
        ) : (
          <>
            <span className="font-medium">Custom</span>
            <span className="text-zinc-400">·</span>
            <span className="italic text-zinc-500">no source</span>
          </>
        )}
      </span>

      {/* Diverged "Edited" badge */}
      {diverged && (
        <span className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
          Edited
        </span>
      )}

      {/* Kebab menu */}
      <div ref={rootRef} className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="More options"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100 transition focus:outline-none focus:ring-2 focus:ring-zinc-400"
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>

        {menuOpen && (
          <div
            role="menu"
            className="absolute left-0 top-full mt-1 w-52 bg-white border border-zinc-200 rounded-lg shadow-lg py-1 z-20"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Reset to source */}
            <button
              type="button"
              role="menuitem"
              disabled={!canReset}
              aria-disabled={!canReset}
              onClick={() => {
                if (!canReset) return
                setMenuOpen(false)
                onReset()
              }}
              className="w-full flex items-center px-3 py-2 text-sm text-left text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Reset to source
            </button>

            {/* Swap source */}
            <button
              type="button"
              role="menuitem"
              disabled={!canSwap}
              aria-disabled={!canSwap}
              onClick={() => {
                if (!canSwap) return
                setMenuOpen(false)
                onSwap()
              }}
              className="w-full flex items-center px-3 py-2 text-sm text-left text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Swap source
            </button>

            <div className="my-1 border-t border-zinc-100" />

            {/* Save as new template */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false)
                onSaveAsTemplate()
              }}
              className="w-full flex items-center px-3 py-2 text-sm text-left text-zinc-700 hover:bg-zinc-50"
            >
              Save as new template
            </button>

            {/* Update source template */}
            <button
              type="button"
              role="menuitem"
              disabled={!canUpdateSource}
              aria-disabled={!canUpdateSource}
              onClick={() => {
                if (!canUpdateSource) return
                setMenuOpen(false)
                onUpdateSourceTemplate()
              }}
              className="w-full flex items-center px-3 py-2 text-sm text-left text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Update source template
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
