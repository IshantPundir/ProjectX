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
