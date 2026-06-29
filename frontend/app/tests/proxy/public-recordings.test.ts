import { describe, expect, it, vi } from 'vitest'

// Mock @supabase/ssr so proxy() runs with NO authenticated user.
// /recordings/ is no longer a special public path — it must be auth-gated like
// every other dashboard route.
vi.mock('@supabase/ssr', () => ({
  createServerClient: () => ({
    auth: {
      getUser: async () => ({ data: { user: null }, error: null }),
      getSession: async () => ({ data: { session: null } }),
    },
  }),
}))

import { NextRequest } from 'next/server'

import { proxy } from '@/proxy'

describe('proxy — /recordings/ is now a gated route (moved to session app)', () => {
  it('redirects /recordings/<token> to /login when unauthenticated', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/recordings/abc123'))
    const res = await proxy(req)
    const location = res.headers.get('location')
    expect(location).not.toBeNull()
    expect(location).toContain('/login')
  })

  it('still redirects other protected routes to /login when unauthenticated', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/jobs'))
    const res = await proxy(req)
    const location = res.headers.get('location')
    expect(location).not.toBeNull()
    expect(location).toContain('/login')
  })
})
