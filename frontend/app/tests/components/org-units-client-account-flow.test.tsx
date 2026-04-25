import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { renderWithProviders } from '../_utils/render'

const pushMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, refresh: vi.fn() }),
}))

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: async () => 'stub-token',
}))

const listUnitsMock = vi.fn()
const createMock = vi.fn()
vi.mock('@/lib/api/org-units', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/org-units')>(
    '@/lib/api/org-units',
  )
  return {
    ...actual,
    orgUnitsApi: {
      ...actual.orgUnitsApi,
      list: () => listUnitsMock(),
      create: (_t: string, body: unknown) => createMock(body),
    },
  }
})

vi.mock('@/lib/api/jobs', () => ({
  jobsApi: { list: async () => [] },
}))

// workspace_mode: 'agency' is required for client_account to be a creatable
// unit type — see UNIT_TYPES filter in the page.
vi.mock('@/lib/api/auth', () => ({
  authApi: {
    me: async () => ({
      user_id: 'u',
      email: 'admin@example.com',
      full_name: null,
      tenant_id: 't',
      client_name: 'Acme',
      is_super_admin: true,
      onboarding_complete: true,
      has_org_units: true,
      workspace_mode: 'agency',
      assignments: [],
    }),
  },
}))

import OrgUnitsPage from '@/app/(dashboard)/settings/org-units/page'

describe('OrgUnitsPage client_account flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Empty units list so the parent_unit_id select is not rendered, keeping
    // the form minimal (just name + type).
    listUnitsMock.mockResolvedValue([])
    createMock.mockReset()
  })

  it('opens CompanyProfileDialog on client_account submit, then creates with the merged profile', async () => {
    createMock.mockResolvedValue({
      id: 'new-client-1',
      client_id: 't',
      parent_unit_id: null,
      name: 'TestClient',
      unit_type: 'client_account',
      member_count: 0,
      created_at: '2026-01-01T00:00:00Z',
      created_by: null,
      created_by_email: null,
      deletable_by: null,
      deletable_by_email: null,
      admin_delete_disabled: false,
      is_accessible: true,
      admin_emails: [],
      is_root: false,
      company_profile: null,
      company_profile_completed_at: null,
      metadata: null,
    })

    renderWithProviders(<OrgUnitsPage />)
    await screen.findByText(/org structure/i)

    // Open the create form.
    await userEvent.click(
      await screen.findByRole('button', { name: /new unit/i }),
    )

    // Fill name and switch type to client_account. unit_type is rendered as
    // a native <select> (px Select wraps a real <select>), so userEvent
    // selectOptions works directly.
    await userEvent.type(await screen.findByLabelText(/name/i), 'TestClient')
    await userEvent.selectOptions(
      await screen.findByLabelText(/type/i),
      'client_account',
    )

    // Submit the create-unit form. With unit_type === 'client_account',
    // the page should branch into opening the CompanyProfileDialog instead
    // of calling the create mutation directly.
    await userEvent.click(
      await screen.findByRole('button', { name: /^create unit$/i }),
    )

    // Dialog appears with its title.
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Client account profile/i }),
      ).toBeInTheDocument()
    })

    // The mutation must NOT have fired yet — the dialog gates it.
    expect(createMock).not.toHaveBeenCalled()

    // Fill the company profile form. The px Select renders as a native
    // <select>, so fireEvent/userEvent on getByLabelText works for industry
    // and company_stage.
    await userEvent.type(
      screen.getByLabelText(/What does your company actually build/i),
      'We build distributed log processing for real-time analytics at petabyte scale.',
    )
    await userEvent.selectOptions(
      screen.getByLabelText(/^Industry$/i),
      'saas_enterprise_software',
    )
    await userEvent.selectOptions(
      screen.getByLabelText(/^Company stage$/i),
      'series_a_b',
    )
    await userEvent.type(
      screen.getByLabelText(/What does a strong hire look like/i),
      'Pragmatic engineers comfortable with ambiguity and operational ownership.',
    )

    // Submit the profile dialog. The form's submit button is
    // disabled until the form is valid — wait for it to enable.
    const finishBtn = await screen.findByRole('button', {
      name: /Create client account/i,
    })
    await waitFor(() => expect(finishBtn).not.toBeDisabled())
    await userEvent.click(finishBtn)

    // The create mutation should fire exactly once with the merged payload.
    await waitFor(() => {
      expect(createMock).toHaveBeenCalledTimes(1)
    })
    expect(createMock).toHaveBeenCalledWith({
      name: 'TestClient',
      unit_type: 'client_account',
      parent_unit_id: null,
      company_profile: {
        about:
          'We build distributed log processing for real-time analytics at petabyte scale.',
        industry: 'saas_enterprise_software',
        company_stage: 'series_a_b',
        hiring_bar:
          'Pragmatic engineers comfortable with ambiguity and operational ownership.',
      },
    })

    // After success, the page navigates to the new unit detail.
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith('/settings/org-units/new-client-1')
    })
  })
})
