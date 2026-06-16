import { describe, expect, it, vi } from 'vitest'

// Mock @supabase/ssr so proxy() runs with NO authenticated user. The
// /recordings/ branch must short-circuit to a pass-through BEFORE any auth
// check, so even an unauthenticated request is not redirected to /login.
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

describe('proxy — public /recordings/ allowlist', () => {
  it('lets /recordings/<token> through unauthenticated (no /login redirect)', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/recordings/abc123'))
    const res = await proxy(req)
    // Pass-through (NextResponse.next), NOT a redirect to /login.
    expect(res.headers.get('location')).toBeNull()
  })

  it('still redirects a protected route to /login when unauthenticated', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/jobs'))
    const res = await proxy(req)
    const location = res.headers.get('location')
    expect(location).not.toBeNull()
    expect(location).toContain('/login')
  })
})
