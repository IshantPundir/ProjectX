export interface RawMessage {
  id: string
  timestamp: number
  from?: { isLocal?: boolean }
  message: string
}

export interface Turn {
  id: string
  who: 'ai' | 'you'
  text: string
}

/** Map LiveKit ReceivedMessages to ordered turns, dropping empties. */
export function toTurns(messages: RawMessage[]): Turn[] {
  const turns: Turn[] = []
  for (const m of messages) {
    const text = (m.message ?? '').trim()
    if (!text) continue
    turns.push({ id: m.id, who: m.from?.isLocal ? 'you' : 'ai', text })
  }
  return turns
}

/** The most recent AI (remote) line — used for the spoken caption. */
export function latestSpokenLine(messages: RawMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (!m.from?.isLocal) {
      const text = (m.message ?? '').trim()
      if (text) return text
    }
  }
  return null
}
