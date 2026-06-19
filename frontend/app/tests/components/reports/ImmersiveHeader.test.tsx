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
  expect(screen.getByRole('button', { name: /candidate reel/i })).toBeInTheDocument()
})

test('hides the reel button on reject', () => {
  render(<ImmersiveHeader header={header} verdict="reject" hasReel onOpenReel={() => {}} onOpenSession={() => {}} />)
  expect(screen.queryByRole('button', { name: /candidate reel/i })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})
