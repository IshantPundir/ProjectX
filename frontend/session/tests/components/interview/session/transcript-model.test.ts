import { describe, expect, it } from 'vitest'
import { toTurns, latestSpokenLine, type RawMessage } from '@/components/interview/session/transcript-model'

const msg = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id,
  timestamp: Number(id),
  from: { isLocal },
  message,
})

describe('toTurns', () => {
  it('maps local messages to "you" and remote to "ai", preserving order', () => {
    const turns = toTurns([msg('1', false, 'Hello'), msg('2', true, 'Hi there')])
    expect(turns).toEqual([
      { id: '1', who: 'ai', text: 'Hello' },
      { id: '2', who: 'you', text: 'Hi there' },
    ])
  })
  it('ignores empty/whitespace messages', () => {
    expect(toTurns([msg('1', false, '   ')])).toEqual([])
  })
})

describe('latestSpokenLine', () => {
  it('returns the most recent AI (remote) message text', () => {
    expect(latestSpokenLine([msg('1', false, 'First'), msg('2', true, 'me'), msg('3', false, 'Second')]))
      .toBe('Second')
  })
  it('returns null when there is no AI message', () => {
    expect(latestSpokenLine([msg('1', true, 'me')])).toBeNull()
  })
})
