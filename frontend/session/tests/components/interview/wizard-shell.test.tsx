import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { WizardShell } from '@/app/interview/[token]/WizardShell'
import { candidateSessionApi, type PreCheckResponse } from '@/lib/api/candidate-session'

vi.mock('@/components/agents-ui/aura', () => ({ Aura: () => <div data-testid="aura" /> }))
// Keep the heavy live App out of these pre-check tests.
vi.mock('@/components/interview/app/app', () => ({ App: () => <div data-testid="live-app" /> }))
// Stub the MediaPipe face gate (no WebGL/WASM in jsdom).
vi.mock('@/components/interview/proctoring/use-precheck-face-gate', () => ({
  usePreCheckFaceGate: () => ({ ready: false, failed: false, faceCount: 0, boxes: [], frame: null }),
}))

const base: PreCheckResponse = {
  session_id: 's1',
  company_name: 'Acme',
  job_title: 'Engineer',
  duration_minutes: 20,
  consent_text: 'consent',
  state: 'created',
  otp_required: false,
  otp_verified_at: null,
  otp_issued_at: null,
  proctoring_enabled: true,
  proctoring_outcome: null,
} as PreCheckResponse

function mockPreCheck(data: PreCheckResponse) {
  return vi.spyOn(candidateSessionApi, 'preCheck').mockResolvedValue(data)
}

afterEach(() => vi.restoreAllMocks())

describe('WizardShell stage derivation', () => {
  it('renders IntroStage for a created session', async () => {
    mockPreCheck(base)
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByRole('heading', { name: /engineer/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /i'm ready/i })).toBeInTheDocument()
  })

  it('renders VerifyStage when consented + otp required + unverified', async () => {
    mockPreCheck({ ...base, state: 'consented', otp_required: true, otp_verified_at: null })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByText(/enter your access code/i)).toBeInTheDocument()
  })

  it('renders ReadyStage when consented + otp satisfied', async () => {
    mockPreCheck({ ...base, state: 'consented', otp_required: false })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByText(/camera check/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start interview/i })).toBeInTheDocument()
  })

  it('mounts the live App (rejoin) for an active session', async () => {
    mockPreCheck({ ...base, state: 'active' })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByTestId('live-app')).toBeInTheDocument()
  })
})
