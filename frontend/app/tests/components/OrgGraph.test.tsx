import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import {
  OrgGraph,
  type GraphNodeData,
} from '@/components/dashboard/org-units/OrgGraph'

function unit(overrides: Partial<GraphNodeData>): GraphNodeData {
  return {
    id: overrides.id ?? 'x',
    client_id: 't1',
    parent_unit_id: null,
    name: 'X',
    unit_type: 'division',
    member_count: 0,
    created_at: '2026-04-01T00:00:00Z',
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
    company_profile_completion_status: 'complete',
    metadata: null,
    inherited_locale: null,
    inherited_compliance: null,
    openRoles: 0,
    pressure: 'cool',
    ...overrides,
  }
}

const TREE: GraphNodeData[] = [
  unit({ id: 'co', name: 'BinQle', unit_type: 'company', is_root: true }),
  unit({ id: 'na', name: 'NA', unit_type: 'region', parent_unit_id: 'co' }),
  unit({
    id: 'eng',
    name: 'Engineering',
    unit_type: 'division',
    parent_unit_id: 'na',
  }),
]

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  window.localStorage.clear()
})

describe('OrgGraph', () => {
  it('renders one card per unit', () => {
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    )
    // Each card has its name visible.
    expect(screen.getByText('BinQle')).toBeInTheDocument()
    expect(screen.getByText('NA')).toBeInTheDocument()
    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('calls onSelect with the clicked unit id', () => {
    const onSelect = vi.fn()
    renderWithProviders(
      <OrgGraph units={TREE} selectedId={null} onSelect={onSelect} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /region: NA/ }))
    expect(onSelect).toHaveBeenCalledWith('na')
  })

  it('calls onOpen with the unit id on double-click', () => {
    const onOpen = vi.fn()
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
        onOpen={onOpen}
      />,
    )
    fireEvent.doubleClick(
      screen.getByRole('button', { name: /division: Engineering/ }),
    )
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith('eng')
  })

  it('does not throw on double-click when onOpen is omitted', () => {
    renderWithProviders(
      <OrgGraph units={TREE} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(() => {
      fireEvent.doubleClick(
        screen.getByRole('button', { name: /division: Engineering/ }),
      )
    }).not.toThrow()
  })

  it('marks the selected card and its ancestors with on-path / selected states', () => {
    renderWithProviders(
      <OrgGraph units={TREE} selectedId="eng" onSelect={vi.fn()} />,
    )
    expect(
      screen.getByRole('button', { name: /division: Engineering/ }),
    ).toHaveAttribute('data-state', 'selected')
    expect(
      screen.getByRole('button', { name: /region: NA/ }),
    ).toHaveAttribute('data-state', 'on-path')
    expect(
      screen.getByRole('button', { name: /company: BinQle/ }),
    ).toHaveAttribute('data-state', 'on-path')
  })

  it('persists the chosen direction to localStorage', () => {
    renderWithProviders(
      <OrgGraph units={TREE} selectedId={null} onSelect={vi.fn()} />,
    )
    const group = screen.getByRole('group', { name: /Layout direction/i })
    fireEvent.click(within(group).getByRole('button', { name: /Left.*Right/i }))
    expect(window.localStorage.getItem('org-graph-direction')).toBe('LR')
    expect(
      within(group).getByRole('button', { name: /Left.*Right/i }),
    ).toHaveAttribute('aria-pressed', 'true')
  })

  it('reads the persisted direction on mount', () => {
    window.localStorage.setItem('org-graph-direction', 'LR')
    renderWithProviders(
      <OrgGraph units={TREE} selectedId={null} onSelect={vi.fn()} />,
    )
    const group = screen.getByRole('group', { name: /Layout direction/i })
    expect(
      within(group).getByRole('button', { name: /Left.*Right/i }),
    ).toHaveAttribute('aria-pressed', 'true')
  })

  it('warns when more than one root unit is present', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const twoRoots: GraphNodeData[] = [
      unit({ id: 'a', name: 'A', unit_type: 'company', is_root: true }),
      unit({ id: 'b', name: 'B', unit_type: 'company', is_root: true }),
    ]
    renderWithProviders(
      <OrgGraph units={twoRoots} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('expected one root unit'),
    )
    warn.mockRestore()
  })

  it('opens the radial menu on right-click of a node', () => {
    renderWithProviders(
      <OrgGraph units={TREE} selectedId={null} onSelect={vi.fn()} />,
    )
    const card = screen.getByRole('button', { name: /region: NA/ })
    fireEvent.contextMenu(card)
    expect(
      screen.getByRole('menu', { name: /Actions for NA/i }),
    ).toBeInTheDocument()
  })

  it('calls onDelete with the unit id when Delete is picked', () => {
    const onDelete = vi.fn()
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
        onDelete={onDelete}
      />,
    )
    fireEvent.contextMenu(screen.getByRole('button', { name: /region: NA/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Delete NA/i }))
    expect(onDelete).toHaveBeenCalledWith('na')
  })

  it('fires onPickChild with parent id and child type once the menu retracts', async () => {
    const onPickChild = vi.fn()
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
        onPickChild={onPickChild}
      />,
    )
    fireEvent.contextMenu(screen.getByRole('button', { name: /region: NA/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Add Team/i }))
    // The pick fires through the menu's exit animation, so wait for it
    // to reach the consumer rather than asserting synchronously.
    await waitFor(() => {
      expect(onPickChild).toHaveBeenCalledWith('na', 'team')
    })
  })
})
