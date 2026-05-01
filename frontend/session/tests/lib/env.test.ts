import { describe, expect, it } from 'vitest'
import { z } from 'zod'

import { envSchema } from '@/lib/env'

describe('envSchema', () => {
  it('accepts a valid http URL', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'http://localhost:8000',
    })
    expect(parsed.NEXT_PUBLIC_API_URL).toBe('http://localhost:8000')
  })

  it('accepts a valid https URL', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://api.projectx.com',
    })
    expect(parsed.NEXT_PUBLIC_API_URL).toBe('https://api.projectx.com')
  })

  it('rejects a missing NEXT_PUBLIC_API_URL', () => {
    expect(() => envSchema.parse({})).toThrow(z.ZodError)
  })

  it('rejects a non-URL string', () => {
    expect(() =>
      envSchema.parse({ NEXT_PUBLIC_API_URL: 'not-a-url' }),
    ).toThrow(z.ZodError)
  })

  it('rejects an empty string', () => {
    expect(() => envSchema.parse({ NEXT_PUBLIC_API_URL: '' })).toThrow(
      z.ZodError,
    )
  })
})
