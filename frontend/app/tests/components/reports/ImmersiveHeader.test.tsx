import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ImmersiveHeader } from '@/components/dashboard/reports/ImmersiveHeader'
import type { ReportHeader } from '@/lib/api/reports'

const header: ReportHeader = {
  candidate_name: 'Punar Sharma', candidate_email: 'punar@example.com',
  job_title: 'EMM Engineer', stage_label: 'AI Screening',
  session_started_at: '2026-06-18T14:14:00Z', duration_seconds: 840,
  skills: ['Intune', 'Troubleshooting'], reference_photo_url: null,
}

test('shows identity, email, job and skills', () => {
  render(<ImmersiveHeader header={header} verdict="advance" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.getByText('Punar Sharma')).toBeInTheDocument()
  expect(screen.getByText('punar@example.com')).toBeInTheDocument()
  expect(screen.getByText('Intune')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /candidate highlight/i })).toBeInTheDocument()
})

test('hides the reel button on reject', () => {
  render(<ImmersiveHeader header={header} verdict="reject" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.queryByRole('button', { name: /candidate highlight/i })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})

test('hasReel=false hides reel button even on advance verdict', () => {
  render(<ImmersiveHeader header={header} verdict="advance" hasReel={false} onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.queryByRole('button', { name: /candidate highlight/i })).toBeNull()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})

test('null date and duration render without NaN or Invalid Date', () => {
  const nullHeader = { ...header, session_started_at: null, duration_seconds: null }
  render(<ImmersiveHeader header={nullHeader as never} verdict="advance" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(document.body.textContent).not.toMatch(/NaN|Invalid Date/)
  expect(screen.getByText('Punar Sharma')).toBeInTheDocument()
})

test('renders worn SVG verdict stamp with correct text for each verdict', () => {
  const { unmount } = render(<ImmersiveHeader header={header} verdict="advance" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.getByText('APPROVED')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: approved/i })).toBeInTheDocument()
  unmount()

  const { unmount: u2 } = render(<ImmersiveHeader header={header} verdict="borderline" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.getByText('BORDERLINE')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: borderline/i })).toBeInTheDocument()
  u2()

  render(<ImmersiveHeader header={header} verdict="reject" hasReel={false} onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.getByText('REJECTED')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /verdict: rejected/i })).toBeInTheDocument()
})
