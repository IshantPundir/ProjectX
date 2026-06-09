/**
 * proxy.ts — CSP connect-src LiveKit origin coverage
 *
 * Spec §4.3 (docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md)
 * requires tests asserting that the candidate app's CSP correctly includes the
 * self-hosted LiveKit origin allowance in connect-src under all three cases:
 *   1. Dev mode: `ws://localhost:*` is included
 *   2. No LIVEKIT_WS_URL: falls back to the Cloud wildcards (back-compat)
 *   3. LIVEKIT_WS_URL set: the custom origin is used
 *
 * Also asserts the NEXT_PUBLIC_API_URL guard throws when the var is unset.
 *
 * Tests call the real `proxy()` function — no mocking of the CSP logic.
 */
import { NextRequest } from 'next/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Import is deferred inside each test so that `process.env` mutations
// via `vi.stubEnv` are already in place when the module is evaluated.
// ESM module caching means we must re-import between tests to avoid
// stale snapshots, so we use a helper that bypasses the module cache.
async function importProxy() {
  // Append a unique cache-buster to force a fresh module evaluation so
  // that `process.env` reads inside the function body see the stubbed values.
  // The function reads env at call-time (not module load-time), so a single
  // import is actually sufficient here — but re-importing keeps the test
  // contract clean and future-proof if module-level reads are ever added.
  const mod = await import('../proxy?t=' + Date.now())
  return mod.proxy
}

function makeRequest(url = 'https://app.example.com/interview/abc') {
  return new NextRequest(url)
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('proxy() — CSP connect-src', () => {
  it('includes ws://localhost:* AND http://localhost:* in dev mode', async () => {
    vi.stubEnv('NODE_ENV', 'development')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com')
    vi.stubEnv('NEXT_PUBLIC_LIVEKIT_WS_URL', 'wss://livekit.example.com')

    const proxy = await importProxy()
    const response = proxy(makeRequest())
    const csp = response.headers.get('Content-Security-Policy')

    // The LiveKit client opens the ws socket AND issues an http(s) validate
    // fetch to the same host — connect-src must allow both for the self-hosted SFU.
    expect(csp).toContain('ws://localhost:*')
    expect(csp).toContain('http://localhost:*')
  })

  it('uses the Cloud wildcard fallback when NEXT_PUBLIC_LIVEKIT_WS_URL is unset', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com')
    // Deliberately do NOT stub NEXT_PUBLIC_LIVEKIT_WS_URL

    const proxy = await importProxy()
    const response = proxy(makeRequest())
    const csp = response.headers.get('Content-Security-Policy')

    expect(csp).toContain('wss://*.livekit.cloud')
    expect(csp).toContain('https://*.livekit.cloud')
  })

  it('uses the self-hosted origin when NEXT_PUBLIC_LIVEKIT_WS_URL is set', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com')
    vi.stubEnv('NEXT_PUBLIC_LIVEKIT_WS_URL', 'wss://livekit.example.com')

    const proxy = await importProxy()
    const response = proxy(makeRequest())
    const csp = response.headers.get('Content-Security-Policy')

    expect(csp).toContain('wss://livekit.example.com')
    // Cloud wildcard should NOT appear when a custom origin is configured
    expect(csp).not.toContain('wss://*.livekit.cloud')
  })

  it('throws when NEXT_PUBLIC_API_URL is unset', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    // Ensure the var is not set
    vi.stubEnv('NEXT_PUBLIC_API_URL', '')

    const proxy = await importProxy()
    expect(() => proxy(makeRequest())).toThrow('NEXT_PUBLIC_API_URL must be set')
  })

  it('does not include ws://localhost:* in production mode', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com')
    vi.stubEnv('NEXT_PUBLIC_LIVEKIT_WS_URL', 'wss://livekit.example.com')

    const proxy = await importProxy()
    const response = proxy(makeRequest())
    const csp = response.headers.get('Content-Security-Policy')

    expect(csp).not.toContain('ws://localhost:*')
  })

  // Regression: `upgrade-insecure-requests` would rewrite the candidate's
  // plaintext `ws://localhost:7880` LiveKit connection to `wss://`, which the
  // non-TLS self-hosted dev SFU can't answer — breaking the room. It must be
  // omitted in dev and present in prod (where everything is already TLS).
  it('omits upgrade-insecure-requests in dev mode', async () => {
    vi.stubEnv('NODE_ENV', 'development')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8000')

    const proxy = await importProxy()
    const response = proxy(makeRequest('http://localhost:3002/interview/abc'))
    const csp = response.headers.get('Content-Security-Policy')

    expect(csp).not.toContain('upgrade-insecure-requests')
  })

  it('includes upgrade-insecure-requests in production mode', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com')

    const proxy = await importProxy()
    const response = proxy(makeRequest())
    const csp = response.headers.get('Content-Security-Policy')

    expect(csp).toContain('upgrade-insecure-requests')
  })
})
