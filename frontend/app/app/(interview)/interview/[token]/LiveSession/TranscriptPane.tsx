'use client'

import { useChat } from '@livekit/components-react'

export function TranscriptPane() {
  const { chatMessages } = useChat()
  return (
    <aside className="border-t border-zinc-200 max-h-64 overflow-y-auto p-4 space-y-2 bg-white">
      {chatMessages.map((m) => (
        <div
          key={m.id}
          className={`text-sm ${m.from?.identity?.startsWith('agent-') ? 'text-zinc-700' : 'text-zinc-900 font-medium'}`}
        >
          <span className="opacity-60 mr-2">
            {m.from?.identity?.startsWith('agent-') ? 'Interviewer' : 'You'}
          </span>
          {m.message}
        </div>
      ))}
    </aside>
  )
}
