'use client'

import { useAgentState } from './hooks/use-agent-state'

export function AgentTile() {
  const state = useAgentState()
  return (
    <div className="rounded-2xl bg-zinc-900 p-6 flex flex-col items-center justify-center aspect-video">
      <div className="size-24 rounded-full bg-zinc-800 grid place-items-center mb-3">
        <div
          className={`size-3 rounded-full transition-all ${state === 'speaking' ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'}`}
        />
      </div>
      <p className="text-zinc-200 text-sm">Interviewer · {state}</p>
    </div>
  )
}
