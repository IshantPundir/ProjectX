// app/interview/[token]/InstructionList.tsx
'use client'

import { useState, type ComponentType, type SVGProps } from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

export interface Instruction {
  id: string
  Icon: ComponentType<SVGProps<SVGSVGElement>>
  title: string
  detail: string
  /** 'caution' gets a distinct (reassuring, not alarming) accent. */
  tone?: 'default' | 'caution'
}

/**
 * Interactive instruction list. Each row is a button that expands a one-line
 * "why" (progressive disclosure). The detail stays in the DOM and collapses via
 * a grid-rows 0fr→1fr transition — no JS height measurement, no layout-thrash.
 */
export function InstructionList({ items }: { items: Instruction[] }) {
  return (
    <ul className="flex flex-col gap-1.5" aria-label="What to know before you start">
      {items.map((item) => (
        <InstructionRow key={item.id} item={item} />
      ))}
    </ul>
  )
}

function InstructionRow({ item }: { item: Instruction }) {
  const [open, setOpen] = useState(false)
  const tone = item.tone ?? 'default'
  const { Icon } = item
  return (
    <li>
      <button
        type="button"
        data-tone={tone}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'group flex w-full items-center gap-3 rounded-[12px] border px-3.5 py-3 text-left',
          'min-h-[44px] transition-colors duration-200',
          'border-px-hairline bg-px-surface/60 hover:bg-px-surface',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-px-accent',
        )}
      >
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-[9px]',
            tone === 'caution'
              ? 'bg-[var(--px-caution-bg)] text-px-caution'
              : 'bg-[var(--px-accent-tint)] text-px-accent',
          )}
        >
          <Icon />
        </span>
        <span className="flex-1 text-[15px] font-medium text-px-fg">{item.title}</span>
        <ChevronDown
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-px-fg-4 transition-transform duration-200',
            open && 'rotate-180',
          )}
        />
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <p className="px-3.5 pt-1.5 pb-1 pl-[60px] text-[13.5px] leading-relaxed text-px-fg-3">
            {item.detail}
          </p>
        </div>
      </div>
    </li>
  )
}
