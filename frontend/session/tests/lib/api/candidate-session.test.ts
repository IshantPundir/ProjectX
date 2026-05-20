import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('candidateSessionApi error handling', () => {
  it('only copies whitelisted fields onto the thrown error', async () => {
    const malicious = {
      detail: 'invalid otp',
      code: 'OTP_INVALID',
      attempts_remaining: 2,
      retry_after_seconds: 30,
      // Attacker-supplied keys that would otherwise shadow Error fields.
      stack: 'pwned',
      name: 'PwnedError',
      message: 'pwned',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(malicious), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    try {
      await candidateSessionApi.verifyOtp('tok', { code: '000000' })
      throw new Error('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(Error)
      const e = err as Error & Record<string, unknown>
      expect(e.name).toBe('Error')
      expect(e.stack).not.toBe('pwned')
      expect(e.message).toBe('invalid otp')
      expect(e.code).toBe('OTP_INVALID')
      expect(e.attempts_remaining).toBe(2)
      expect(e.retry_after_seconds).toBe(30)
    }
  })
})

describe('candidateSessionApi.proctoringEvent', () => {
  it('POSTs the violation and returns the parsed result', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ terminated: false, violation_count: 1, soft_violation_count: 1 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const res = await candidateSessionApi.proctoringEvent('tok', {
      kind: 'keyboard',
      occurred_at: '2026-05-21T00:00:00.000Z',
    })

    expect(res.terminated).toBe(false)
    expect(res.violation_count).toBe(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/candidate-session/tok/proctoring/event')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({
      kind: 'keyboard',
      occurred_at: '2026-05-21T00:00:00.000Z',
    })
  })
})
