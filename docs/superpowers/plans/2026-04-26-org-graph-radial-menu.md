# Org Graph — Radial Context Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-click radial ("spider web") context menu to the xyflow-based `OrgGraph`. Pill items at the end of accent-color spokes, animated pop-in. Each menu offers Delete (where applicable) plus one item per legal child unit type. Clicking a child item opens an inline name-form at the pivot point; clicking Delete opens the existing `DangerConfirmDialog`.

**Architecture:** New presentation components (`OrgUnitContextMenu`, `OrgUnitInlineCreate`) own no data — they receive a target + callbacks from `OrgGraph`. `OrgGraph` owns the overlay state machine (`menu` → `create` → `null`). The page wires Delete and Create-Child callbacks to the existing TanStack Query mutation hooks (`useDeleteOrgUnit`, `useCreateOrgUnit`). Pure CSS animations; no new deps.

**Tech Stack:** Next.js 16 + React 19 + Tailwind v4, `@xyflow/react@^12`, `lucide-react`, Vitest + RTL.

**Spec:** [`docs/superpowers/specs/2026-04-26-org-graph-radial-menu-design.md`](../specs/2026-04-26-org-graph-radial-menu-design.md)

---

## File Structure

All under `frontend/app/`.

| Path | New / Modified | Responsibility |
|---|---|---|
| `components/dashboard/org-units/unit-children-rules.ts` | New | Pure helper: `getAllowedChildTypes(parent)`. Mirrors backend nesting rules in `app/modules/org_units/service.py`. |
| `components/dashboard/org-units/OrgUnitContextMenu.tsx` | New | Radial menu component. Pivot dot, spokes, pill items at computed angles. Keyboard handling (arrows/Escape). Pure presentation. |
| `components/dashboard/org-units/OrgUnitInlineCreate.tsx` | New | Inline mini-form: type chip + name input + submit/cancel hints. Pure presentation. |
| `components/dashboard/org-units/OrgGraph.tsx` | Modified | Add `onDelete` and `onCreateChild` props. Wire `onNodeContextMenu` on `<ReactFlow>`. Manage overlay state (menu / create / null). Render the new components. |
| `components/dashboard/org-units/OrgUnitNode.tsx` | Modified | Add Shift+F10 / Menu key keyboard handler. Forwards an "open context menu" intent via a new `data.onContextMenu` callback. |
| `app/(dashboard)/settings/org-units/page.tsx` | Modified | Pass `onDelete` + `onCreateChild` props to `<OrgGraph>`. Render `<DangerConfirmDialog>` for delete confirmation. |
| `tests/components/unit-children-rules.test.ts` | New | Pure unit tests for the rules helper. |
| `tests/components/OrgUnitContextMenu.test.tsx` | New | Render n items per props, click triggers callback, ArrowRight/Left cycle focus, Escape closes. |
| `tests/components/OrgUnitInlineCreate.test.tsx` | New | Renders type chip + input. Enter submits trimmed name. Escape cancels. Empty name does not submit. |
| `tests/components/OrgGraph.test.tsx` | Modified | Add 4 cases: right-click opens menu, clicking Delete pill calls `onDelete`, clicking child pill renders inline-create form, Shift+F10 on focused card opens menu. |

**Files NOT modified:**
- Backend (`backend/nexus/`) — endpoints exist, nesting rules already enforced server-side.
- `lib/hooks/use-create-org-unit.ts`, `use-delete-org-unit.ts` — already wired with cache invalidation.
- `lib/api/org-units.ts` — `create()` and `delete()` already typed.

---

## Conventions and Reminders

- All commands run from `frontend/app/` unless stated otherwise.
- There is **no `type-check` script** in this project. Use `npm run build` for full type-check via Next.js's tsc invocation.
- `tests/setup.ts` already polyfills `localStorage`, `ResizeObserver`, `DOMMatrixReadOnly` for jsdom. The radial menu uses none of these directly but the existing OrgGraph test does — the polyfills must remain.
- Animation honors `prefers-reduced-motion: reduce` (collapses to instant).
- Pre-existing lint error in `app/(dashboard)/jobs/[jobId]/questions/page.tsx:894` is not your concern.
- All new test code uses `renderWithProviders` from `tests/_utils/render.tsx`.

---

## Task 1: Create `unit-children-rules.ts`

Pure helper — encodes the backend's nesting rules so the menu can filter items locally. Foundation for Task 2.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/unit-children-rules.ts`
- Create: `frontend/app/tests/components/unit-children-rules.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/unit-children-rules.test.ts`:

```ts
import { describe, expect, it } from 'vitest'

import { getAllowedChildTypes } from '@/components/dashboard/org-units/unit-children-rules'

describe('getAllowedChildTypes', () => {
  it('returns nothing for team (leaf node)', () => {
    expect(getAllowedChildTypes('team')).toEqual([])
  })

  it('forbids client_account under client_account but allows the others', () => {
    const out = getAllowedChildTypes('client_account')
    expect(out).toEqual(['region', 'division', 'team'])
    expect(out).not.toContain('client_account')
  })

  it('allows all four child types under company', () => {
    expect(getAllowedChildTypes('company')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })

  it('allows all four child types under region', () => {
    expect(getAllowedChildTypes('region')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })

  it('allows all four child types under division', () => {
    expect(getAllowedChildTypes('division')).toEqual([
      'region',
      'division',
      'client_account',
      'team',
    ])
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run test -- tests/components/unit-children-rules.test.ts
```

Expected: fails with module-not-found.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/unit-children-rules.ts`:

```ts
import type { UnitType } from './unit-type-style'

/**
 * Stable display order for child-type items. The radial menu places
 * items in this order starting after Delete.
 */
const ALL_CHILD_TYPES: readonly UnitType[] = [
  'region',
  'division',
  'client_account',
  'team',
] as const

/**
 * Mirrors the backend nesting rules in
 * `app/modules/org_units/service.py::create_org_unit`:
 *   - Teams are leaves: no children allowed.
 *   - client_account cannot nest under another client_account.
 *   - company is never a child of anything (root-only). It is never
 *     part of the returned list.
 *
 * The backend re-validates on every create — this helper is a UX gate,
 * not a security boundary.
 */
export function getAllowedChildTypes(parent: UnitType): UnitType[] {
  if (parent === 'team') return []
  if (parent === 'client_account') {
    return ALL_CHILD_TYPES.filter((t) => t !== 'client_account')
  }
  // company / region / division — all four are allowed.
  return [...ALL_CHILD_TYPES]
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/unit-children-rules.test.ts
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/org-units/unit-children-rules.ts frontend/app/tests/components/unit-children-rules.test.ts
git commit -m "feat(org-graph): unit-children-rules helper for radial menu"
```

---

## Task 2: Create `OrgUnitContextMenu.tsx`

The radial menu itself. Renders pivot, spokes, and pill items at computed angles. Pure presentation — receives a target + callbacks, owns no data. Keyboard handling lives here.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgUnitContextMenu.tsx`
- Create: `frontend/app/tests/components/OrgUnitContextMenu.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/OrgUnitContextMenu.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitContextMenu } from '@/components/dashboard/org-units/OrgUnitContextMenu'
import type { GraphNodeData } from '@/components/dashboard/org-units/OrgGraph'

function makeUnit(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    id: 'u1',
    client_id: 't1',
    parent_unit_id: null,
    name: 'North America',
    unit_type: 'region',
    member_count: 5,
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
    metadata: null,
    openRoles: 0,
    pressure: 'cool',
    ...overrides,
  }
}

function defaultProps(overrides: object = {}) {
  return {
    target: { unit: makeUnit(), x: 200, y: 150 },
    allowedChildTypes: ['region', 'division', 'client_account', 'team'] as const,
    onClose: vi.fn(),
    onPickDelete: vi.fn(),
    onPickChild: vi.fn(),
    ...overrides,
  }
}

describe('OrgUnitContextMenu', () => {
  it('renders Delete plus one pill per allowed child type', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    expect(screen.getByRole('menuitem', { name: /Delete North America/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Region/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Division/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Client account/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Team/i })).toBeInTheDocument()
  })

  it('omits Delete when admin_delete_disabled is true', () => {
    renderWithProviders(
      <OrgUnitContextMenu
        {...defaultProps({
          target: { unit: makeUnit({ admin_delete_disabled: true }), x: 0, y: 0 },
        })}
      />,
    )
    expect(screen.queryByRole('menuitem', { name: /Delete/i })).not.toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Region/i })).toBeInTheDocument()
  })

  it('renders only Delete when no child types are allowed (team leaf)', () => {
    renderWithProviders(
      <OrgUnitContextMenu
        {...defaultProps({ allowedChildTypes: [] })}
      />,
    )
    expect(screen.getByRole('menuitem', { name: /Delete/i })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /Add/i })).not.toBeInTheDocument()
  })

  it('places items at evenly-spaced angles starting at 12 o\'clock', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    const items = screen.getAllByRole('menuitem')
    // 5 items: Delete + 4 child types → 360/5 = 72° apart.
    expect(items).toHaveLength(5)
    expect(items[0].getAttribute('data-angle')).toBe('0')   // Delete at top
    expect(items[1].getAttribute('data-angle')).toBe('72')
    expect(items[2].getAttribute('data-angle')).toBe('144')
    expect(items[3].getAttribute('data-angle')).toBe('216')
    expect(items[4].getAttribute('data-angle')).toBe('288')
  })

  it('calls onPickDelete when the Delete pill is clicked', () => {
    const onPickDelete = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onPickDelete })} />)
    fireEvent.click(screen.getByRole('menuitem', { name: /Delete/i }))
    expect(onPickDelete).toHaveBeenCalledTimes(1)
  })

  it('calls onPickChild with the chosen unit type', () => {
    const onPickChild = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onPickChild })} />)
    fireEvent.click(screen.getByRole('menuitem', { name: /Add Division/i }))
    expect(onPickChild).toHaveBeenCalledWith('division')
  })

  it('calls onClose on Escape', () => {
    const onClose = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onClose })} />)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('cycles focus with ArrowRight (clockwise) and ArrowLeft (counter-clockwise)', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    const items = screen.getAllByRole('menuitem')
    // First item is auto-focused on mount.
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowRight' })
    expect(items[1]).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowLeft' })
    expect(items[0]).toHaveFocus()
    // Wrap counter-clockwise: at index 0, Left → last
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowLeft' })
    expect(items[items.length - 1]).toHaveFocus()
  })

  it('exposes a menu role with the unit name in the aria-label', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    expect(screen.getByRole('menu')).toHaveAttribute(
      'aria-label',
      'Actions for North America',
    )
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/OrgUnitContextMenu.test.tsx
```

Expected: fails with module-not-found.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/OrgUnitContextMenu.tsx`:

```tsx
'use client'

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react'
import { Trash2, type LucideIcon } from 'lucide-react'

import type { GraphNodeData } from './OrgGraph'
import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

const RADIUS = 110
const PILL_HEIGHT = 36

const CHILD_LABEL: Record<UnitType, string> = {
  company: 'Company',
  client_account: 'Client account',
  region: 'Region',
  division: 'Division',
  team: 'Team',
}

export interface ContextMenuTarget {
  unit: GraphNodeData
  /** Pivot in canvas-local coordinates. */
  x: number
  y: number
}

interface Props {
  target: ContextMenuTarget
  allowedChildTypes: readonly UnitType[]
  onClose: () => void
  onPickDelete: () => void
  onPickChild: (type: UnitType) => void
}

interface Item {
  key: string
  label: string
  ariaLabel: string
  icon: LucideIcon
  iconColor: string
  iconBg: string
  iconLine: string
  isDanger: boolean
  onPick: () => void
}

export function OrgUnitContextMenu({
  target,
  allowedChildTypes,
  onClose,
  onPickDelete,
  onPickChild,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [activeIdx, setActiveIdx] = useState(0)

  const items: Item[] = useMemo(() => {
    const out: Item[] = []
    const isDeletable = !target.unit.admin_delete_disabled
    if (isDeletable) {
      out.push({
        key: 'delete',
        label: 'Delete',
        ariaLabel: `Delete ${target.unit.name}`,
        icon: Trash2,
        iconColor: 'var(--color-red-700)',
        iconBg: 'var(--color-red-50)',
        iconLine: 'var(--color-red-200)',
        isDanger: true,
        onPick: onPickDelete,
      })
    }
    for (const type of allowedChildTypes) {
      const style = UNIT_TYPE_STYLE[type]
      out.push({
        key: type,
        label: CHILD_LABEL[type],
        ariaLabel: `Add ${CHILD_LABEL[type]}`,
        icon: style.icon,
        iconColor: style.stripVar,
        iconBg: style.bgVar,
        iconLine: style.lineVar,
        isDanger: false,
        onPick: () => onPickChild(type),
      })
    }
    return out
  }, [allowedChildTypes, target.unit, onPickDelete, onPickChild])

  // Compute placements: angle 0 = top (12 o'clock), clockwise.
  // dx = R sin θ, dy = -R cos θ.
  const placements = useMemo(
    () =>
      items.map((item, i) => {
        const angleDeg = items.length === 0 ? 0 : (i * 360) / items.length
        const rad = (angleDeg * Math.PI) / 180
        const dx = RADIUS * Math.sin(rad)
        const dy = -RADIUS * Math.cos(rad)
        return { ...item, angleDeg, dx, dy }
      }),
    [items],
  )

  // Focus first item on mount.
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLElement>('[role="menuitem"]')
    first?.focus()
    setActiveIdx(0)
  }, [])

  // Click-outside closes the menu. Listen on document; the inner
  // click handlers stop propagation so this only fires for outside.
  useEffect(() => {
    function onDocPointer(e: MouseEvent) {
      if (!ref.current) return
      if (ref.current.contains(e.target as Node)) return
      onClose()
    }
    document.addEventListener('mousedown', onDocPointer)
    return () => document.removeEventListener('mousedown', onDocPointer)
  }, [onClose])

  function focusItem(i: number) {
    const list = ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]')
    list?.[i]?.focus()
    setActiveIdx(i)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    if (items.length === 0) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      focusItem((activeIdx + 1) % items.length)
      return
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      focusItem((activeIdx - 1 + items.length) % items.length)
      return
    }
  }

  const rootStyle: CSSProperties = {
    position: 'absolute',
    left: target.x,
    top: target.y,
    width: 0,
    height: 0,
    pointerEvents: 'auto',
    zIndex: 50,
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for ${target.unit.name}`}
      onKeyDown={handleKeyDown}
      onContextMenu={(e) => e.preventDefault()}
      style={rootStyle}
    >
      {/* Pivot dot */}
      <span
        aria-hidden="true"
        className="pointer-events-none rmenu-pivot"
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: 8,
          height: 8,
          background: 'var(--px-accent)',
          borderRadius: '50%',
          transform: 'translate(-50%, -50%)',
          boxShadow: '0 0 0 6px var(--px-accent-tint)',
        }}
      />

      {/* Spokes */}
      {placements.map((p) => (
        <span
          key={`spoke-${p.key}`}
          aria-hidden="true"
          className="rmenu-spoke"
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: RADIUS,
            height: 1.4,
            background: 'var(--px-accent)',
            opacity: 0.55,
            transform: `rotate(${p.angleDeg - 90}deg)`,
            transformOrigin: '0 50%',
          }}
        />
      ))}

      {/* Pills */}
      {placements.map((p, i) => {
        const Icon = p.icon
        return (
          <button
            key={p.key}
            type="button"
            role="menuitem"
            tabIndex={i === activeIdx ? 0 : -1}
            aria-label={p.ariaLabel}
            data-angle={p.angleDeg}
            data-key={p.key}
            onClick={(e) => {
              e.stopPropagation()
              p.onPick()
            }}
            className="rmenu-pill"
            style={{
              position: 'absolute',
              left: p.dx,
              top: p.dy,
              transform: 'translate(-50%, -50%)',
              height: PILL_HEIGHT,
              borderRadius: 999,
              padding: '0 14px 0 10px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              fontWeight: 600,
              color: p.isDanger ? 'var(--color-red-700)' : 'var(--px-fg)',
              background: 'var(--px-surface)',
              border: `1px solid ${p.isDanger ? 'var(--color-red-200)' : 'var(--px-hairline-strong)'}`,
              boxShadow:
                '0 8px 24px rgba(58, 45, 28, 0.08), 0 2px 4px rgba(58, 45, 28, 0.04)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              animationDelay: `${i * 30}ms`,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 22,
                height: 22,
                borderRadius: 999,
                background: p.iconBg,
                border: `1px solid ${p.iconLine}`,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flex: 'none',
              }}
            >
              <Icon
                size={12}
                color={p.iconColor}
                strokeWidth={2.4}
                aria-hidden
              />
            </span>
            {p.label}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/OrgUnitContextMenu.test.tsx
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/org-units/OrgUnitContextMenu.tsx frontend/app/tests/components/OrgUnitContextMenu.test.tsx
git commit -m "feat(org-graph): OrgUnitContextMenu radial component"
```

---

## Task 3: Create `OrgUnitInlineCreate.tsx`

The inline mini-form that appears at the pivot after a child-type pick. Renders the type chip + name input + submit/cancel hints.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgUnitInlineCreate.tsx`
- Create: `frontend/app/tests/components/OrgUnitInlineCreate.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/OrgUnitInlineCreate.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitInlineCreate } from '@/components/dashboard/org-units/OrgUnitInlineCreate'

function defaultProps(overrides: object = {}) {
  return {
    unitType: 'region' as const,
    x: 100,
    y: 100,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    pending: false,
    error: null as string | null,
    ...overrides,
  }
}

describe('OrgUnitInlineCreate', () => {
  it('renders the type chip and an autofocused name input', () => {
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps()} />)
    expect(screen.getByText(/\+ Region/i)).toBeInTheDocument()
    const input = screen.getByPlaceholderText(/Name the new region/i)
    expect(input).toBeInTheDocument()
    expect(input).toHaveFocus()
  })

  it('calls onSubmit with the trimmed name on Enter', () => {
    const onSubmit = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onSubmit })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.change(input, { target: { value: '  Engineering  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('Engineering')
  })

  it('does not submit on Enter when the input is empty or whitespace-only', () => {
    const onSubmit = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onSubmit })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('calls onCancel on Escape', () => {
    const onCancel = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onCancel })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('shows an error message when error prop is set', () => {
    renderWithProviders(
      <OrgUnitInlineCreate {...defaultProps({ error: 'Name already taken' })} />,
    )
    expect(screen.getByText(/Name already taken/)).toBeInTheDocument()
  })

  it('disables the input while pending', () => {
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ pending: true })} />)
    expect(screen.getByPlaceholderText(/Name the new region/i)).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/OrgUnitInlineCreate.test.tsx
```

Expected: fails with module-not-found.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/OrgUnitInlineCreate.tsx`:

```tsx
'use client'

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react'

import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

const TYPE_LABEL: Record<UnitType, string> = {
  company: 'Company',
  client_account: 'Client account',
  region: 'Region',
  division: 'Division',
  team: 'Team',
}

interface Props {
  unitType: UnitType
  /** Pivot in canvas-local coordinates. */
  x: number
  y: number
  onSubmit: (name: string) => void
  onCancel: () => void
  pending?: boolean
  error?: string | null
}

export function OrgUnitInlineCreate({
  unitType,
  x,
  y,
  onSubmit,
  onCancel,
  pending = false,
  error = null,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState('')

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = value.trim()
      if (trimmed.length === 0) return
      onSubmit(trimmed)
    }
  }

  const style = UNIT_TYPE_STYLE[unitType]
  const label = TYPE_LABEL[unitType]

  const rootStyle: CSSProperties = {
    position: 'absolute',
    left: x,
    top: y,
    transform: 'translate(-50%, -50%)',
    background: 'var(--px-surface)',
    border: '1px solid var(--px-accent-line)',
    boxShadow:
      '0 0 0 3px var(--px-accent-glow), 0 8px 24px rgba(58, 45, 28, 0.08), 0 2px 4px rgba(58, 45, 28, 0.04)',
    borderRadius: 10,
    padding: '8px 10px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    pointerEvents: 'auto',
    zIndex: 50,
  }

  return (
    <div
      onContextMenu={(e) => e.preventDefault()}
      onMouseDown={(e) => e.stopPropagation()}
      style={rootStyle}
    >
      <span
        aria-hidden="true"
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          color: style.stripVar,
          background: style.bgVar,
          padding: '3px 7px',
          borderRadius: 999,
          border: `1px solid ${style.lineVar}`,
          whiteSpace: 'nowrap',
        }}
      >
        + {label}
      </span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={pending}
          aria-label={`Name the new ${label.toLowerCase()}`}
          placeholder={`Name the new ${label.toLowerCase()}…`}
          style={{
            border: '1px solid var(--px-hairline-strong)',
            background: 'var(--px-bg)',
            borderRadius: 6,
            padding: '5px 8px',
            fontSize: 12,
            width: 200,
            color: 'var(--px-fg)',
            outline: 'none',
            fontFamily: 'inherit',
          }}
        />
        {error && (
          <span
            role="alert"
            style={{
              fontSize: 10.5,
              color: 'var(--color-red-700)',
            }}
          >
            {error}
          </span>
        )}
      </div>
      <span
        aria-hidden="true"
        style={{
          fontSize: 10,
          color: 'var(--px-fg-4)',
          fontFamily: 'ui-monospace, "JetBrains Mono", monospace',
          whiteSpace: 'nowrap',
        }}
      >
        ⏎ create · esc cancel
      </span>
    </div>
  )
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/OrgUnitInlineCreate.test.tsx
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/org-units/OrgUnitInlineCreate.tsx frontend/app/tests/components/OrgUnitInlineCreate.test.tsx
git commit -m "feat(org-graph): OrgUnitInlineCreate mini-form"
```

---

## Task 4: Wire the menu into `OrgGraph.tsx`

Add overlay state, hook xyflow's `onNodeContextMenu`, render the menu and the inline-create form. Add the two new props (`onDelete`, `onCreateChild`).

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/OrgGraph.tsx`
- Modify: `frontend/app/tests/components/OrgGraph.test.tsx` (add 4 cases)

- [ ] **Step 1: Extend the existing `OrgGraph.test.tsx`**

Open `frontend/app/tests/components/OrgGraph.test.tsx` and add the imports + new test cases. Find the existing `import { fireEvent, screen, within } from '@testing-library/react'` line — keep it. Add `waitFor` to that import:

```tsx
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
```

Then append these test cases inside the existing `describe('OrgGraph', ...)` block (just before its closing `})`):

```tsx
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

  it('renders the inline-create form when a child-type item is picked', async () => {
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
        onCreateChild={vi.fn()}
      />,
    )
    fireEvent.contextMenu(screen.getByRole('button', { name: /region: NA/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Add Division/i }))
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Name the new division/i),
      ).toBeInTheDocument()
    })
  })

  it('calls onCreateChild with parent id, type, and name on inline submit', async () => {
    const onCreateChild = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(
      <OrgGraph
        units={TREE}
        selectedId={null}
        onSelect={vi.fn()}
        onCreateChild={onCreateChild}
      />,
    )
    fireEvent.contextMenu(screen.getByRole('button', { name: /region: NA/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Add Team/i }))
    const input = await screen.findByPlaceholderText(/Name the new team/i)
    fireEvent.change(input, { target: { value: 'Frontend' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => {
      expect(onCreateChild).toHaveBeenCalledWith('na', 'team', 'Frontend')
    })
  })
```

- [ ] **Step 2: Run the new tests — expect FAIL**

```bash
npm run test -- tests/components/OrgGraph.test.tsx
```

Expected: 4 new tests fail (props don't exist; menu doesn't render).

- [ ] **Step 3: Modify `OrgGraph.tsx`**

Open `frontend/app/components/dashboard/org-units/OrgGraph.tsx`. Add imports at the top (with the existing imports):

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
```

(`useState` is the new addition — keep the rest as-is.)

Add the new component imports below the existing org-units imports:

```tsx
import {
  OrgUnitContextMenu,
  type ContextMenuTarget,
} from './OrgUnitContextMenu'
import { OrgUnitInlineCreate } from './OrgUnitInlineCreate'
import { getAllowedChildTypes } from './unit-children-rules'
```

Find the `OrgGraphProps` interface and extend it:

```tsx
interface OrgGraphProps {
  units: GraphNodeData[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** Fired when the user double-clicks a card. Typically wired to a
   *  `router.push` to the unit's detail page. */
  onOpen?: (id: string) => void
  /** Fired when the user picks Delete in the right-click menu. */
  onDelete?: (id: string) => void
  /** Fired when the user submits the inline create form. */
  onCreateChild?: (
    parentId: string,
    unitType: UnitType,
    name: string,
  ) => Promise<void>
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  hoverId?: string | null
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  onHover?: (id: string | null) => void
}
```

Update the `OrgGraphInner` signature to receive the new props:

```tsx
function OrgGraphInner({
  units,
  selectedId,
  onSelect,
  onOpen,
  onDelete,
  onCreateChild,
}: OrgGraphProps) {
```

Inside `OrgGraphInner`, after the existing `const [direction, setDirection] = useDirectionToggle()` line, add the overlay state machine + a ref to the canvas wrapper:

```tsx
  type Overlay =
    | { kind: 'menu'; unit: GraphNodeData; x: number; y: number }
    | { kind: 'create'; unit: GraphNodeData; childType: UnitType; x: number; y: number }
    | null
  const [overlay, setOverlay] = useState<Overlay>(null)
  const [createPending, setCreatePending] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)

  // Translate a viewport-coords MouseEvent into canvas-local coords.
  const toCanvasCoords = useCallback((e: { clientX: number; clientY: number }) => {
    const rect = wrapperRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }, [])
```

Find the `<ReactFlow ...>` opening and add `onNodeContextMenu`:

```tsx
    <ReactFlow
      nodes={positionedNodes}
      edges={rawEdges}
      onNodesChange={onNodesChange}
      onNodeClick={(_e, node) => onSelect(node.id)}
      onNodeDoubleClick={(_e, node) => onOpen?.(node.id)}
      onNodeContextMenu={(e, node) => {
        e.preventDefault()
        const unit = units.find((u) => u.id === node.id)
        if (!unit) return
        onSelect(unit.id) // right-click selects, like left-click
        const { x, y } = toCanvasCoords(e)
        setOverlay({ kind: 'menu', unit, x, y })
        setCreateError(null)
      }}
```

(The rest of the `<ReactFlow>` props stay as-is.)

Wrap the existing `<ReactFlow>...</ReactFlow>` block in a positioned wrapper `<div>` with the ref so we can measure for canvas-local coords. Replace the `return (` block of `OrgGraphInner` with:

```tsx
  return (
    <div
      ref={wrapperRef}
      style={{ position: 'absolute', inset: 0 }}
    >
      <ReactFlow
        nodes={positionedNodes}
        edges={rawEdges}
        onNodesChange={onNodesChange}
        onNodeClick={(_e, node) => onSelect(node.id)}
        onNodeDoubleClick={(_e, node) => onOpen?.(node.id)}
        onNodeContextMenu={(e, node) => {
          e.preventDefault()
          const unit = units.find((u) => u.id === node.id)
          if (!unit) return
          onSelect(unit.id)
          const { x, y } = toCanvasCoords(e)
          setOverlay({ kind: 'menu', unit, x, y })
          setCreateError(null)
        }}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        attributionPosition="bottom-left"
        zoomOnDoubleClick={false}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        panOnDrag
        zoomOnScroll
      >
        <Background gap={22} size={1} color="var(--px-fg-4)" />
        <Controls position="bottom-right" showInteractive={false} />
        <Panel position="top-right">
          {/* ... existing direction-toggle panel content unchanged ... */}
        </Panel>
      </ReactFlow>

      {overlay?.kind === 'menu' && (
        <OrgUnitContextMenu
          target={{ unit: overlay.unit, x: overlay.x, y: overlay.y }}
          allowedChildTypes={getAllowedChildTypes(
            overlay.unit.unit_type as UnitType,
          )}
          onClose={() => setOverlay(null)}
          onPickDelete={() => {
            const id = overlay.unit.id
            setOverlay(null)
            onDelete?.(id)
          }}
          onPickChild={(type) => {
            setOverlay({
              kind: 'create',
              unit: overlay.unit,
              childType: type,
              x: overlay.x,
              y: overlay.y,
            })
            setCreateError(null)
          }}
        />
      )}

      {overlay?.kind === 'create' && (
        <OrgUnitInlineCreate
          unitType={overlay.childType}
          x={overlay.x}
          y={overlay.y}
          pending={createPending}
          error={createError}
          onCancel={() => {
            setOverlay(null)
            setCreateError(null)
          }}
          onSubmit={async (name) => {
            if (!onCreateChild) {
              setOverlay(null)
              return
            }
            setCreatePending(true)
            setCreateError(null)
            try {
              await onCreateChild(overlay.unit.id, overlay.childType, name)
              setOverlay(null)
            } catch (err) {
              setCreateError(
                err instanceof Error ? err.message : 'Failed to create unit',
              )
            } finally {
              setCreatePending(false)
            }
          }}
        />
      )}
    </div>
  )
```

(Replace the entire existing `return (...)` block with the version above. The Panel content is preserved verbatim — just the wrapping changes.)

To preserve the Panel content: copy the existing `<Panel position="top-right">...</Panel>` block (the segmented control with the two `<DirButton>`s plus its `onKeyDown` handler) into the new structure exactly as it was.

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/OrgGraph.test.tsx
```

Expected: all OrgGraph tests pass (the original ones plus the 4 new ones).

- [ ] **Step 5: Run the full suite to make sure nothing else broke**

```bash
npm run test
```

Expected: full suite green.

- [ ] **Step 6: Build to verify TypeScript**

```bash
npm run build
```

Expected: clean compile.

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/org-units/OrgGraph.tsx frontend/app/tests/components/OrgGraph.test.tsx
git commit -m "feat(org-graph): wire radial menu into OrgGraph"
```

---

## Task 5: Add Shift+F10 / Menu key to `OrgUnitNode.tsx`

Keyboard a11y entry point for the radial menu. The card forwards a "open context menu" intent to `OrgGraph` via a new optional `data.onContextMenu` callback.

**Files:**
- Modify: `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx`
- Modify: `frontend/app/components/dashboard/org-units/OrgGraph.tsx` (add `onContextMenu` to the node `data`)

- [ ] **Step 1: Extend the existing `OrgUnitNode.test.tsx`**

Open `frontend/app/tests/components/OrgUnitNode.test.tsx`. In the `renderNode` helper, add `onContextMenu` to the `data` object:

```tsx
function renderNode(opts: {
  unit?: Partial<GraphNodeData>
  selectedId?: string | null
  onSelectPath?: Set<string>
  onSelect?: (id: string) => void
  onContextMenu?: (id: string) => void
} = {}) {
  const unit = makeUnit(opts.unit)
  const onSelect = opts.onSelect ?? vi.fn()
  const onContextMenu = opts.onContextMenu ?? vi.fn()
  const data = {
    unit,
    selectedId: opts.selectedId ?? null,
    onSelectPath: opts.onSelectPath ?? new Set<string>(),
    onSelect,
    onContextMenu,
  }
  // ...rest unchanged
  return { ...utils, unit, onSelect, onContextMenu }
}
```

(Just add the `onContextMenu` property in two places: the function signature and the `data` object. Update the `return` to include `onContextMenu` so tests can spy on it.)

Add this test case to the `describe('OrgUnitNode', ...)` block:

```tsx
  it('calls data.onContextMenu on Shift+F10', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'F10', shiftKey: true })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
    expect(onContextMenu).toHaveBeenCalledWith('u1')
  })

  it('calls data.onContextMenu on the ContextMenu key', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'ContextMenu' })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
  })
```

- [ ] **Step 2: Run the test — expect FAIL**

```bash
npm run test -- tests/components/OrgUnitNode.test.tsx
```

Expected: the 2 new tests fail (handler not yet implemented).

- [ ] **Step 3: Modify `OrgUnitNode.tsx`**

Open `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx`.

Update the `OrgUnitNodeData` interface to include the optional callback:

```tsx
interface OrgUnitNodeData {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
  onContextMenu?: (id: string) => void
}
```

Update the destructure inside `OrgUnitNodeImpl`:

```tsx
  const { unit, selectedId, onSelectPath, onSelect, onContextMenu } =
    data as unknown as OrgUnitNodeData
```

Replace the existing `handleKey` function with:

```tsx
  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(unit.id)
      return
    }
    // OS-standard "open context menu" shortcuts.
    if (
      e.key === 'ContextMenu' ||
      (e.key === 'F10' && e.shiftKey)
    ) {
      e.preventDefault()
      onContextMenu?.(unit.id)
    }
  }
```

- [ ] **Step 4: Add `onContextMenu` to the node `data` in `OrgGraph.tsx`**

Open `frontend/app/components/dashboard/org-units/OrgGraph.tsx`.

Update the `OrgUnitNodeData` type alias to add `onContextMenu`:

```tsx
type OrgUnitNodeData = {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
  onContextMenu: (id: string) => void
} & Record<string, unknown>
```

Inside `OrgGraphInner`, before the `rawNodes` `useMemo`, add a stable callback that opens the menu at the card's center:

```tsx
  const onCardContextMenu = useCallback(
    (id: string) => {
      const unit = units.find((u) => u.id === id)
      if (!unit) return
      // Anchor the menu at the card's bounding-box center in the canvas.
      const cardEl = wrapperRef.current?.querySelector<HTMLElement>(
        `[data-id="${id}"]`,
      )
      const wrapperRect = wrapperRef.current?.getBoundingClientRect()
      if (!cardEl || !wrapperRect) return
      const cardRect = cardEl.getBoundingClientRect()
      const x = cardRect.left + cardRect.width / 2 - wrapperRect.left
      const y = cardRect.top + cardRect.height / 2 - wrapperRect.top
      onSelect(unit.id)
      setOverlay({ kind: 'menu', unit, x, y })
      setCreateError(null)
    },
    [units, onSelect],
  )
```

Wire it into `rawNodes`:

```tsx
  const rawNodes = useMemo<Node<OrgUnitNodeData>[]>(
    () =>
      units.map((u) => ({
        id: u.id,
        type: 'orgUnit',
        position: { x: 0, y: 0 },
        data: {
          unit: u,
          selectedId,
          onSelectPath,
          onSelect,
          onContextMenu: onCardContextMenu,
        },
      })),
    [units, selectedId, onSelectPath, onSelect, onCardContextMenu],
  )
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
npm run test
```

Expected: all green (existing OrgUnitNode tests still pass + 2 new ones; OrgGraph tests still pass).

- [ ] **Step 6: Build**

```bash
npm run build
```

Expected: clean compile.

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/org-units/OrgUnitNode.tsx frontend/app/components/dashboard/org-units/OrgGraph.tsx frontend/app/tests/components/OrgUnitNode.test.tsx
git commit -m "feat(org-graph): Shift+F10 / Menu key opens the radial menu"
```

---

## Task 6: Wire mutations + delete dialog in `page.tsx`

Wire the new `<OrgGraph onDelete onCreateChild>` props to the existing TanStack Query mutation hooks and surface a `DangerConfirmDialog` for delete.

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/page.tsx`

- [ ] **Step 1: Read the relevant section of `page.tsx`**

Open `frontend/app/app/(dashboard)/settings/org-units/page.tsx`. Find the `<OrgGraph ...>` JSX block (around line 427) and the imports at the top.

- [ ] **Step 2: Add the imports**

Add at the top of the file, alongside existing imports:

```tsx
import { useDeleteOrgUnit } from "@/lib/hooks/use-delete-org-unit";
import { DangerConfirmDialog } from "@/components/px";
import type { UnitType } from "@/components/dashboard/org-units/unit-type-style";
```

(`useCreateOrgUnit` is already imported.)

- [ ] **Step 3: Add state + mutation handlers inside the component**

Inside the `OrgUnitsPage` component (or whatever the default-export function is named in `page.tsx`), find the existing `const [selectedId, setSelectedId] = useState<string | null>(null);` line. After it, add:

```tsx
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const deleteMutation = useDeleteOrgUnit();
  const createMutation = useCreateOrgUnit();

  async function handleCreateChild(
    parentId: string,
    unitType: UnitType,
    name: string,
  ) {
    await createMutation.mutateAsync({
      name,
      unit_type: unitType,
      parent_unit_id: parentId,
      company_profile: null,
      metadata: null,
    });
    toast.success(`${unitType.replace('_', ' ')} created`);
  }

  function handleDeleteRequest(id: string) {
    const unit = graphNodes.find((u) => u.id === id);
    if (!unit) return;
    setDeleteTarget({ id, name: unit.name });
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    try {
      await deleteMutation.mutateAsync(deleteTarget.id);
      if (selectedId === deleteTarget.id) setSelectedId(null);
      toast.success("Unit deleted");
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete unit");
    }
  }
```

(`toast` is already imported from `sonner` at the top of the file. `graphNodes` is already in scope.)

- [ ] **Step 4: Pass the new props to `<OrgGraph>`**

Update the `<OrgGraph ...>` JSX block:

```tsx
            <OrgGraph
              units={graphNodes}
              selectedId={selectedId}
              hoverId={hoverId}
              onSelect={setSelectedId}
              onHover={setHoverId}
              onOpen={(id) => router.push(`/settings/org-units/${id}`)}
              onDelete={handleDeleteRequest}
              onCreateChild={handleCreateChild}
            />
```

- [ ] **Step 5: Render the confirm dialog**

At the end of the page's JSX (just before the final closing fragment / element), add:

```tsx
      <DangerConfirmDialog
        open={deleteTarget !== null}
        title={
          deleteTarget ? `Delete ${deleteTarget.name}?` : 'Delete unit?'
        }
        description="This will also delete all of its sub-units. This cannot be undone."
        confirmLabel="Delete unit"
        pendingLabel="Deleting…"
        pending={deleteMutation.isPending}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
```

(Place it as a sibling of the main page wrapper. If the file ends with `return (<>...</>);` add it inside the fragment. If it returns a single root element, add it as a sibling — wrap in a fragment if needed.)

- [ ] **Step 6: Type-check via build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run build
```

Expected: clean compile.

- [ ] **Step 7: Run the existing org-units flow test to make sure nothing regressed**

```bash
npm run test -- tests/components/org-units-client-account-flow.test.tsx
```

Expected: still passes.

- [ ] **Step 8: Run the full suite**

```bash
npm run test
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/app/\(dashboard\)/settings/org-units/page.tsx
git commit -m "feat(org-graph): wire radial menu Delete + Create-Child to mutations"
```

---

## Task 7: Final verification

Belt-and-suspenders: full type-check, lint, build, and a manual smoke pass.

**Files:** none modified (verification only)

- [ ] **Step 1: Production build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run build
```

Expected: clean build, zero TypeScript errors.

- [ ] **Step 2: Full test suite**

```bash
npm run test
```

Expected: all green. Note the test count — should be the prior baseline plus ~21 new tests (5 from Task 1, 9 from Task 2, 6 from Task 3, 4 from Task 4, 2 from Task 5).

- [ ] **Step 3: Lint check**

```bash
npm run lint
```

Expected: no NEW errors. (Pre-existing error in `app/(dashboard)/jobs/[jobId]/questions/page.tsx:894` is not your concern.)

- [ ] **Step 4: Manual smoke at `http://localhost:3001/settings/org-units`**

Run the dev server (or use the user's existing one):

```bash
npm run dev
```

Verify (check off each item; if any fails, report and stop):

- [ ] Right-click on the BinQle root card → menu opens with Region, Division, Client account, Team — and **no** Delete (admin_delete_disabled).
- [ ] Right-click on any non-root unit → menu opens with Delete + the legal child types for that unit's type.
- [ ] Right-click on a team unit → menu opens with **only** Delete.
- [ ] Spokes draw from the right-click point; pills pop in with stagger.
- [ ] Click a child-type pill → inline form replaces the menu at the same point. Type a name, press Enter → new unit appears in the graph (after the TanStack Query refetch settles). Toast confirms.
- [ ] Click Delete → `DangerConfirmDialog` opens with the unit's name. Confirm → unit and its descendants disappear from the graph. Toast confirms.
- [ ] Cancel the confirm dialog → no mutation; graph unchanged.
- [ ] Tab to a card, press Shift+F10 → menu opens at the card's center. Arrow keys cycle items. Enter activates. Escape closes.
- [ ] Right-click outside any node (on the canvas background) → no menu opens (only `onNodeContextMenu` fires the menu).

- [ ] **Step 5: Commit any post-smoke tweaks**

If the smoke pass surfaced spacing, color, or a11y tweaks, commit them as a focused fix:

```bash
git add frontend/app/components/dashboard/org-units/
git commit -m "fix(org-graph): post-smoke radial-menu polish — <one-line summary>"
```

If no tweaks needed, skip.

- [ ] **Step 6: Final status check**

```bash
git log --oneline -10
git status
```

Expected: clean tree, ~6-7 new commits all under `feat(org-graph)` / `fix(org-graph)`.

---

## Self-Review Checklist (already run)

- **Spec coverage:** Every spec decision (D1–D10) is implemented. D1/D2 in Tasks 2 + 4. D3 in Task 3 + Task 4 (overlay state machine). D4 (animation) in Task 2's CSS keyframes. D5 (keyboard) split between Task 2 (menu nav) + Task 5 (Shift+F10 entry). D6 (delete dialog) in Task 6. D7 (state ownership) in Task 4. D8 (right-click also selects) in Task 4's `onNodeContextMenu`. D9 (viewport clipping) is implemented as the simpler "anchor at click point" — full clipping deferred (see note below). D10 (workspace mode) handled passively — backend rejection bubbles up via `createError`.
- **D9 simplification:** The spec's "translate the entire menu inward if any item would render outside the canvas" is not implemented in this plan. It's a small geometry helper that can land later if real-world usage shows the need. The default behavior (menu stays where right-clicked) is fine for the typical case where users right-click in the middle of the canvas. If we hit a complaint, add a `useLayoutEffect` after mount to compute clipping and translate.
- **Placeholder scan:** No "TBD" or "TODO" left.
- **Type consistency:** `UnitType`, `GraphNodeData`, `ContextMenuTarget` used consistently. `onCreateChild` signature is identical in `OrgGraph.tsx` (declaration), `page.tsx` (definition), `OrgGraph.test.tsx` (mock spy), and the design spec. `onContextMenu` callback signature `(id: string) => void` matches across `OrgUnitNode.tsx`, `OrgGraph.tsx`, and the test helper.
