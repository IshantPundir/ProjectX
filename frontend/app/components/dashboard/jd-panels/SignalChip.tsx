'use client'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { SignalItem } from '@/lib/api/jobs'

const WEIGHT_DOTS: Record<1 | 2 | 3, string> = {
  1: '\u25CF',
  2: '\u25CF\u25CF',
  3: '\u25CF\u25CF\u25CF',
}

/**
 * Provenance-aware chip with weight indicator and knockout badge.
 *
 *   ai_extracted  -> blue solid
 *   ai_inferred   -> amber dashed with tooltip
 *   recruiter     -> green solid
 *
 * Weight shown as dots (1-3). Knockout signals show a red "KO" badge.
 */
export function SignalChip({ item }: { item: SignalItem }) {
  const base =
    'inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border font-medium'

  const chip = (
    <>
      <span className="text-[8px] leading-none opacity-60" aria-label={`weight ${item.weight}`}>
        {WEIGHT_DOTS[item.weight]}
      </span>
      {item.value}
      {item.knockout && (
        <span className="ml-0.5 px-1 py-px text-[9px] font-bold leading-none rounded bg-red-100 text-red-600 border border-red-200">
          KO
        </span>
      )}
    </>
  )

  if (item.source === 'ai_extracted') {
    return (
      <span className={`${base} bg-blue-50 text-blue-700 border-blue-200`}>
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
        {chip}
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
                {chip}
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

  // recruiter
  return (
    <span className={`${base} bg-emerald-50 text-emerald-700 border-emerald-200`}>
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
      {chip}
    </span>
  )
}
