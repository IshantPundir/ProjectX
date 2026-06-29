import { expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ImmersiveHeader, type ImmersiveHeaderProps } from '@/components/dashboard/reports/ImmersiveHeader'
import type { ReportHeader } from '@/lib/api/reports'

const header: ReportHeader = {
  candidate_name: 'Punar Sharma', candidate_email: 'punar@example.com',
  job_title: 'EMM Engineer', stage_label: 'AI Screening',
  session_started_at: '2026-06-18T14:14:00Z', duration_seconds: 840,
  skills: ['Intune', 'Troubleshooting'], reference_photo_url: null,
}

// Default props so each test only overrides what it cares about.
function renderHeader(overrides: Partial<ImmersiveHeaderProps> = {}) {
  const props: ImmersiveHeaderProps = {
    header,
    verdict: 'advance',
    hasReel: true,
    reelEligible: false,
    reelBusy: false,
    onOpenReel: () => {},
    onGenerateReel: () => {},
    onOpenSession: () => {},
    ...overrides,
  }
  return render(<ImmersiveHeader {...props} />)
}

test('shows identity, email, job and skills', () => {
  renderHeader()
  expect(screen.getByText('Punar Sharma')).toBeInTheDocument()
  expect(screen.getByText('punar@example.com')).toBeInTheDocument()
  expect(screen.getByText('Intune')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /highlights/i })).toBeInTheDocument()
})

test('shows the reel button on reject (all verdicts get the reel)', () => {
  renderHeader({ verdict: 'reject' })
  expect(screen.getByRole('button', { name: /highlights/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})

test('hasReel=false hides reel button even on advance verdict', () => {
  renderHeader({ hasReel: false })
  expect(screen.queryByRole('button', { name: /highlights/i })).toBeNull()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})

test('null date and duration render without NaN or Invalid Date', () => {
  const nullHeader = { ...header, session_started_at: null, duration_seconds: null }
  renderHeader({ header: nullHeader as never })
  expect(document.body.textContent).not.toMatch(/NaN|Invalid Date/)
  expect(screen.getByText('Punar Sharma')).toBeInTheDocument()
})

test('renders worn SVG verdict stamp with correct text for each verdict', () => {
  const { unmount } = renderHeader({ verdict: 'advance' })
  expect(screen.getByText('APPROVED')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: approved/i })).toBeInTheDocument()
  unmount()

  const { unmount: u2 } = renderHeader({ verdict: 'borderline' })
  expect(screen.getByText('BORDERLINE')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: borderline/i })).toBeInTheDocument()
  u2()

  renderHeader({ verdict: 'reject', hasReel: false })
  expect(screen.getByText('REJECTED')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: rejected/i })).toBeInTheDocument()
})

// ─── Generate Highlights CTA ──────────────────────────────────────────────

test('shows the generate button when reel is absent + eligible (advance)', () => {
  const onGenerateReel = vi.fn()
  renderHeader({ hasReel: false, reelEligible: true, onGenerateReel })
  const btn = screen.getByRole('button', { name: /generate highlights/i })
  expect(btn).toBeInTheDocument()
  fireEvent.click(btn)
  expect(onGenerateReel).toHaveBeenCalledOnce()
})

test('shows the generate button on borderline too', () => {
  renderHeader({ verdict: 'borderline', hasReel: false, reelEligible: true })
  expect(screen.getByRole('button', { name: /generate highlights/i })).toBeInTheDocument()
})

test('shows the generate button on reject when eligible (reject no longer blocked)', () => {
  renderHeader({ verdict: 'reject', hasReel: false, reelEligible: true })
  expect(screen.getByRole('button', { name: /generate highlights/i })).toBeInTheDocument()
})

test('hides the generate button when not eligible', () => {
  renderHeader({ hasReel: false, reelEligible: false })
  expect(screen.queryByRole('button', { name: /generate highlights/i })).toBeNull()
})

test('hides the generate button once a reel exists (shows play instead)', () => {
  renderHeader({ hasReel: true, reelEligible: true })
  expect(screen.queryByRole('button', { name: /generate highlights/i })).toBeNull()
  expect(screen.getByRole('button', { name: /highlights/i })).toBeInTheDocument()
})

test('busy state disables the button and shows Generating…', () => {
  const onGenerateReel = vi.fn()
  renderHeader({ hasReel: false, reelEligible: true, reelBusy: true, onGenerateReel })
  const btn = screen.getByRole('button', { name: /generate highlights/i })
  expect(btn).toBeDisabled()
  expect(btn).toHaveTextContent(/generating/i)
  fireEvent.click(btn)
  expect(onGenerateReel).not.toHaveBeenCalled()
})
