'use client'

import type { StarterTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { useStarterPack } from '@/lib/hooks/use-starter-pack'

type Props = {
  onUse: (starter: StarterTemplate) => void
}

export function StarterPackBrowser({ onUse }: Props) {
  const { data: starters, isLoading, isError } = useStarterPack()

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading starter pack…</div>
  }

  if (isError) {
    return (
      <div className="text-sm text-red-600">
        Failed to load starter pack. Please try again.
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {starters?.map((starter) => (
        <div key={starter.key} className="bg-white border border-zinc-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-zinc-900 mb-1">{starter.name}</h3>
          <p className="text-xs text-zinc-500 mb-3">{starter.description}</p>
          <div className="text-xs text-zinc-600 mb-4">
            {starter.stages.length} stage{starter.stages.length !== 1 ? 's' : ''}:{' '}
            {starter.stages.map((s) => s.name).join(' → ')}
          </div>
          <Button size="sm" onClick={() => onUse(starter)}>
            Use this
          </Button>
        </div>
      ))}
    </div>
  )
}
