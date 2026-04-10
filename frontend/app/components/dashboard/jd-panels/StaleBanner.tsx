'use client'

import { Button } from '@/components/ui/button'

type Props = {
  isStale: boolean
  isEnriching: boolean
  enrichmentError: string | null
  onReEnrich: () => void
  onRetry: () => void
}

export function StaleBanner({
  isStale,
  isEnriching,
  enrichmentError,
  onReEnrich,
  onRetry,
}: Props) {
  // Error state: red banner with retry
  if (enrichmentError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-red-700">
          <span className="font-medium">Enrichment failed:</span>
          <span>{enrichmentError}</span>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onRetry}
          className="shrink-0 border-red-300 text-red-700 hover:bg-red-100"
        >
          Retry
        </Button>
      </div>
    )
  }

  // Enriching state: blue animated pill
  if (isEnriching) {
    return (
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
        </span>
        <span className="text-sm font-medium text-blue-700">
          Re-enriching JD...
        </span>
      </div>
    )
  }

  // Stale state: amber banner with re-enrich button
  if (isStale) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 flex items-center justify-between gap-3">
        <span className="text-sm text-amber-800">
          Signals have been updated since the last enrichment.
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onReEnrich}
          className="shrink-0 border-amber-300 text-amber-800 hover:bg-amber-100"
        >
          Re-enrich JD
        </Button>
      </div>
    )
  }

  // Nothing to show
  return null
}
