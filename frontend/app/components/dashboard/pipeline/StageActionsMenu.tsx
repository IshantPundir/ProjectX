'use client'

import { useEffect, useRef, useState } from 'react'
import { MoreVertical, Settings2, Trash2 } from 'lucide-react'

type Props = {
  onEdit: () => void
  onDelete?: () => void
}

export function StageActionsMenu({ onEdit, onDelete }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        aria-label="Stage actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100 transition focus:outline-none focus:ring-2 focus:ring-zinc-400"
      >
        <MoreVertical className="w-4 h-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 w-44 bg-white border border-zinc-200 rounded-lg shadow-lg py-1 z-20"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onEdit()
            }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-700 hover:bg-zinc-50 text-left"
          >
            <Settings2 className="w-4 h-4" />
            Configure
          </button>
          {onDelete && (
            <>
              <div className="my-1 border-t border-zinc-100" />
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  onDelete()
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 text-left"
              >
                <Trash2 className="w-4 h-4" />
                Delete stage
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
