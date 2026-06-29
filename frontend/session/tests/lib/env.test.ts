import { describe, expect, it } from 'vitest'
import { z } from 'zod'

import { envSchema } from '@/lib/env'

describe('NEXT_PUBLIC_PROCTORING_DEBUG', () => {
  it("defaults to false when unset", () => {
    const parsed = envSchema.parse({ NEXT_PUBLIC_API_URL: 'https://x.test' })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(false)
  })

  it("is true only for the literal '1'", () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://x.test',
      NEXT_PUBLIC_PROCTORING_DEBUG: '1',
    })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(true)
  })

  it("is false for any other string", () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://x.test',
      NEXT_PUBLIC_PROCTORING_DEBUG: 'true',
    })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(false)
  })
})

describe('NEXT_PUBLIC_LIVEKIT_WS_URL', () => {
  it('defaults LIVEKIT_WS_URL to the cloud wildcards when unset', () => {
    const parsed = envSchema.parse({ NEXT_PUBLIC_API_URL: 'https://api.example.com' })
    expect(parsed.NEXT_PUBLIC_LIVEKIT_WS_URL).toBe('wss://*.livekit.cloud https://*.livekit.cloud')
  })

  it('uses a provided self-hosted LIVEKIT_WS_URL', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://api.example.com',
      NEXT_PUBLIC_LIVEKIT_WS_URL: 'wss://livekit.example.com',
    })
    expect(parsed.NEXT_PUBLIC_LIVEKIT_WS_URL).toBe('wss://livekit.example.com')
  })

  it('falls back to the cloud wildcards when LIVEKIT_WS_URL is an empty string', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://api.example.com',
      NEXT_PUBLIC_LIVEKIT_WS_URL: '',
    })
    expect(parsed.NEXT_PUBLIC_LIVEKIT_WS_URL).toBe('wss://*.livekit.cloud https://*.livekit.cloud')
  })
})

describe('NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN', () => {
  const base = { NEXT_PUBLIC_API_URL: 'https://api.example.com' }

  it('defaults to the R2 wildcard when unset', () => {
    const env = envSchema.parse({ ...base })
    expect(env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN).toBe('https://*.r2.cloudflarestorage.com')
  })

  it('passes through an explicit origin', () => {
    const env = envSchema.parse({ ...base, NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN: 'https://x.s3.us-east-1.amazonaws.com' })
    expect(env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN).toBe('https://x.s3.us-east-1.amazonaws.com')
  })
})

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
