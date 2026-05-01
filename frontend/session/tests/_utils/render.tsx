import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement } from 'react'

/**
 * Shared render harness for composition + integration tests.
 *
 * Mounts a fresh QueryClient per render with retries off and gcTime: 0
 * so each test gets a clean cache and failed queries do not loop. Add
 * additional providers here as the test surface grows.
 */
export function renderWithProviders(
  ui: ReactElement,
  opts?: RenderOptions,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    opts,
  )
}
