'use client'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { SignalItem } from '@/lib/api/jobs'

/**
 * Provenance-aware chip following Q14 of the brainstorming session:
 * subtle tinted fill + color dot prefix. Inferred chips get a dashed
 * border and an inference_basis tooltip on hover.
 *
 *   ai_extracted → blue solid
 *   ai_inferred  → amber dashed with tooltip
 *   recruiter    → green solid (unused in 2A)
 */
export function SignalChip({ item }: { item: SignalItem }) {
  const base =
    'inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border font-medium'

  if (item.source === 'ai_extracted') {
    return (
      <span className={`${base} bg-blue-50 text-blue-700 border-blue-200`}>
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
        {item.value}
      </span>
    )
  }

  if (item.source === 'ai_inferred') {
    return (
      // Base UI shadcn v4: TooltipProvider uses `delay` (not `delayDuration`).
      // TooltipTrigger uses `render` prop (not `asChild`) to replace the default
      // <button> with our styled <span>.
      <TooltipProvider delay={150}>
        <Tooltip>
          <TooltipTrigger
            render={
              <span
                className={`${base} bg-amber-50 text-amber-800 border border-dashed border-amber-400 cursor-default`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                {item.value}
              </span>
            }
          />
          <TooltipContent className="max-w-xs text-xs">
            <p className="font-semibold mb-1">AI-inferred signal</p>
            <p className="mb-1">
              {item.inference_basis || 'No inference basis provided.'}
            </p>
            <p className="italic text-zinc-400">Verify before confirming.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  // recruiter — not used in 2A (read-only review) but supported for 2B
  return (
    <span className={`${base} bg-emerald-50 text-emerald-700 border-emerald-200`}>
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
      {item.value}
    </span>
  )
}
