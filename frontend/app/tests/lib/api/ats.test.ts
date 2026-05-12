import { describe, expect, it } from 'vitest'

import {
  ceipalCredentialsSchema,
  connectionCreateSchema,
} from '@/lib/api/ats'

describe('ceipalCredentialsSchema', () => {
  it('accepts a valid credentials payload', () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: 'recruiter@example.com',
      password: 'hunter2',
      api_key: 'sk_live_abc123',
    })
    expect(result.success).toBe(true)
  })

  it('rejects an invalid email', () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: 'not-an-email',
      password: 'hunter2',
      api_key: 'sk_live_abc123',
    })
    expect(result.success).toBe(false)
  })

  it('rejects an empty password', () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: 'recruiter@example.com',
      password: '',
      api_key: 'sk_live_abc123',
    })
    expect(result.success).toBe(false)
  })

  it('rejects an empty api_key', () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: 'recruiter@example.com',
      password: 'hunter2',
      api_key: '',
    })
    expect(result.success).toBe(false)
  })
})

describe('connectionCreateSchema', () => {
  it('accepts a ceipal connection payload', () => {
    const result = connectionCreateSchema.safeParse({
      vendor: 'ceipal',
      credentials: {
        email: 'recruiter@example.com',
        password: 'hunter2',
        api_key: 'sk_live_abc123',
      },
    })
    expect(result.success).toBe(true)
  })

  it('rejects an unknown vendor', () => {
    const result = connectionCreateSchema.safeParse({
      vendor: 'greenhouse',
      credentials: {
        email: 'recruiter@example.com',
        password: 'hunter2',
        api_key: 'sk_live_abc123',
      },
    })
    expect(result.success).toBe(false)
  })
})
