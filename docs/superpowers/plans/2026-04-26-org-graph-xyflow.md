# Org Graph — xyflow + dagre Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-rolled SVG `OrgGraph.tsx` with `@xyflow/react` + `@dagrejs/dagre`, adding per-type colors and shapes, a direction toggle (TB/LR), pan/zoom/fitView, and full design-system integration. Read-only — no graph-editing mutations.

**Architecture:** TanStack Query (`useOrgUnits()`) is canonical; a `useMemo` derives raw nodes/edges; `useDagreLayout()` runs dagre and returns positioned nodes; xyflow renders in controlled mode with a no-op `onNodesChange`. Custom node + edge components consume a `data` payload that includes the selected-path set so highlight styling is pure-derivation. Direction state lives in a small `localStorage`-synced hook.

**Tech Stack:** Next.js 16 + React 19 + Tailwind v4, `@xyflow/react@^12`, `@dagrejs/dagre@^1`, Vitest + RTL.

**Spec:** [`docs/superpowers/specs/2026-04-26-org-graph-xyflow-design.md`](../specs/2026-04-26-org-graph-xyflow-design.md)

---

## File Structure

All new/modified files are under `frontend/app/`.

| Path | New / Modified | Responsibility |
|---|---|---|
| `package.json` | Modified | Add `@xyflow/react` and `@dagrejs/dagre` deps. |
| `package-lock.json` | Modified | npm-managed lockfile after install. |
| `components/dashboard/org-units/unit-type-style.tsx` | New | Per-type color + glyph mapping. `UNIT_TYPE_STYLE` map, `getUnitTypeStyle()` with fallback + warn, `Glyph` component. |
| `components/dashboard/org-units/use-direction-toggle.ts` | New | `useDirectionToggle()` hook — `localStorage('org-graph-direction')`-synced state. |
| `components/dashboard/org-units/use-dagre-layout.ts` | New | `useDagreLayout()` hook + pure `getDagreLayout()` for testability. Hardcoded `NODE_WIDTH=168`, `NODE_HEIGHT=52`. |
| `components/dashboard/org-units/OrgUnitNode.tsx` | New | xyflow custom node component (the card). Renders strip + glyph + name + subtitle + open-roles badge. `React.memo`'d. |
| `components/dashboard/org-units/OrgUnitEdge.tsx` | New | xyflow custom edge — `BezierEdge` wrapped with selected-path styling. |
| `components/dashboard/org-units/OrgGraph.tsx` | **Replaced in place** (was 428 LOC) | Public entry. Same exports (`OrgGraph`, `OrgLegend`, `GraphNodeData`, `Pressure`) and same prop shape — `page.tsx` needs zero changes. |
| `tests/components/use-dagre-layout.test.ts` | New | Pure unit tests for `getDagreLayout()`. |
| `tests/components/OrgUnitNode.test.tsx` | New | Card-in-isolation tests. |
| `tests/components/OrgGraph.test.tsx` | New | Composition tests — n cards for n units, direction toggle persistence, selection propagation. |

**Files NOT modified:**
- `app/(dashboard)/settings/org-units/page.tsx` — D15 in spec; same imports keep working.
- Backend, RLS, types in `lib/api/org-units.ts` — out of scope.
- `frontend/app/CLAUDE.md` line about `OrgUnitCanvas.tsx` — D16 in spec; that line becomes accurate after this plan lands.

---

## Conventions and Reminders

- All commands run from `frontend/app/` unless stated otherwise. The dev shell is bash.
- Vitest config: `vitest.config.ts` already wires `jsdom` + `tests/setup.ts` (which imports `@testing-library/jest-dom/vitest`). No config changes needed.
- `renderWithProviders()` lives at `tests/_utils/render.tsx` and mounts a fresh `QueryClientProvider`.
- New components must use the project's `--px-*` tokens for colors. Tailwind utility classes like `bg-blue-50` resolve to the warm-light remapped palette via `@theme` in `globals.css`.
- TypeScript strict mode is on — no `any`. Use `unknown` + narrowing, or precise generics.
- Commit early and often: every task ends with a commit. Use the existing repo style: `feat(org-graph): <summary>` or `test(org-graph): <summary>`.

---

## Task 1: Add xyflow and dagre dependencies

**Files:**
- Modify: `frontend/app/package.json`
- Modify: `frontend/app/package-lock.json`

- [ ] **Step 1: Install the runtime dependencies**

```bash
cd frontend/app
npm install @xyflow/react@^12 @dagrejs/dagre@^1
```

Expected: package.json gains both entries under `dependencies`; package-lock.json updates; no install errors.

- [ ] **Step 2: Verify import resolves**

Run a quick type-check; this catches missing types or a broken peer-dep install.

```bash
npm run type-check
```

Expected: zero errors. (We haven't imported xyflow anywhere yet, so this is just a baseline sanity check.)

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json
git commit -m "feat(org-graph): add @xyflow/react and @dagrejs/dagre deps"
```

---

## Task 2: Create `unit-type-style.tsx`

This file is the single source of truth for every visual difference between the five unit types. Putting the SVG `<Glyph>` component here (alongside the type → style map) lets the legend and node share rendering.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/unit-type-style.tsx`
- Create: `frontend/app/tests/components/unit-type-style.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/unit-type-style.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

import {
  UNIT_TYPE_STYLE,
  getUnitTypeStyle,
  Glyph,
  type UnitType,
} from '@/components/dashboard/org-units/unit-type-style'

describe('UNIT_TYPE_STYLE', () => {
  it('has an entry for each of the five unit types', () => {
    const expected: UnitType[] = [
      'company',
      'client_account',
      'region',
      'division',
      'team',
    ]
    for (const t of expected) {
      expect(UNIT_TYPE_STYLE[t]).toBeDefined()
      expect(UNIT_TYPE_STYLE[t].stripVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].bgVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].lineVar).toMatch(/^var\(--/)
    }
  })

  it('maps each type to a unique glyph kind', () => {
    const glyphs = Object.values(UNIT_TYPE_STYLE).map((s) => s.glyph)
    expect(new Set(glyphs).size).toBe(glyphs.length)
  })
})

describe('getUnitTypeStyle', () => {
  it('returns the typed style for a known unit type', () => {
    expect(getUnitTypeStyle('region')).toBe(UNIT_TYPE_STYLE.region)
  })

  it('falls back to team style and warns for an unknown type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = getUnitTypeStyle('not_a_real_type')
    expect(result).toBe(UNIT_TYPE_STYLE.team)
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('unknown unit_type'),
    )
    warn.mockRestore()
  })
})

describe('Glyph', () => {
  it('renders an SVG with the requested fill color', () => {
    const { container } = render(<Glyph kind="circle" color="#abc123" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute('aria-hidden')).toBe('true')
    expect(container.innerHTML).toContain('#abc123')
  })

  it('renders different shape elements per glyph kind', () => {
    const { container: c1 } = render(<Glyph kind="square" color="#000" />)
    expect(c1.querySelector('rect')).toBeInTheDocument()

    const { container: c2 } = render(<Glyph kind="circle" color="#000" />)
    expect(c2.querySelector('circle')).toBeInTheDocument()

    const { container: c3 } = render(<Glyph kind="diamond" color="#000" />)
    expect(c3.querySelector('polygon')).toBeInTheDocument()

    const { container: c4 } = render(<Glyph kind="hexagon" color="#000" />)
    expect(c4.querySelector('polygon')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/unit-type-style.test.tsx
```

Expected: tests fail with module-not-found or undefined-export errors.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/unit-type-style.tsx`:

```tsx
import type { ReactNode } from 'react'

export type UnitType =
  | 'company'
  | 'client_account'
  | 'region'
  | 'division'
  | 'team'

export type GlyphKind =
  | 'square'
  | 'diamond'
  | 'hexagon'
  | 'pill'
  | 'circle'

export interface UnitTypeStyle {
  /** CSS var for the 4px side strip + glyph fill. */
  stripVar: string
  /** CSS var for the glyph plate background. */
  bgVar: string
  /** CSS var for the glyph plate border. */
  lineVar: string
  /** Shape rendered inside the glyph plate. */
  glyph: GlyphKind
}

/**
 * Per-type visual mapping. All values resolve to existing tokens —
 * Tailwind ramps are remapped to the warm-light palette via @theme in
 * globals.css, so these vars stay consistent with the rest of the
 * dashboard.
 */
export const UNIT_TYPE_STYLE: Record<UnitType, UnitTypeStyle> = {
  company: {
    stripVar: 'var(--color-blue-600)',
    bgVar: 'var(--color-blue-50)',
    lineVar: 'var(--color-blue-200)',
    glyph: 'square',
  },
  client_account: {
    stripVar: 'var(--color-purple-600)',
    bgVar: 'var(--color-purple-50)',
    lineVar: 'var(--color-purple-100)',
    glyph: 'diamond',
  },
  region: {
    stripVar: 'var(--color-amber-700)',
    bgVar: 'var(--color-amber-50)',
    lineVar: 'var(--color-amber-100)',
    glyph: 'hexagon',
  },
  division: {
    stripVar: 'var(--px-accent-2)',
    bgVar: 'var(--color-green-50)',
    lineVar: 'var(--color-green-100)',
    glyph: 'pill',
  },
  team: {
    stripVar: 'var(--px-fg)',
    bgVar: 'var(--px-bg-2)',
    lineVar: 'var(--px-hairline-strong)',
    glyph: 'circle',
  },
} as const

const FALLBACK_STYLE: UnitTypeStyle = UNIT_TYPE_STYLE.team

/**
 * Look up the style for an org unit type. If the backend ever ships a
 * sixth type before the frontend updates this map, fall back to the
 * team style and emit a single console warning so the bug surfaces.
 */
export function getUnitTypeStyle(type: string): UnitTypeStyle {
  if (type in UNIT_TYPE_STYLE) {
    return UNIT_TYPE_STYLE[type as UnitType]
  }
  console.warn(
    `OrgGraph: unknown unit_type "${type}", falling back to team style`,
  )
  return FALLBACK_STYLE
}

/**
 * Renders a small SVG shape filled with `color`. Used both inside the
 * node card (color = strip color, large glyph plate) and inside the
 * legend (color = strip color, small inline icon).
 */
export function Glyph({
  kind,
  color,
  size = 14,
}: {
  kind: GlyphKind
  color: string
  size?: number
}): ReactNode {
  const half = size / 2
  const viewBox = `-${half} -${half} ${size} ${size}`

  switch (kind) {
    case 'square':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <rect
            x={-half + 2}
            y={-half + 2.5}
            width={size - 4}
            height={size - 5}
            rx={2}
            fill={color}
          />
        </svg>
      )
    case 'diamond':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <polygon
            points={`0,${-half + 2} ${half - 2},0 0,${half - 2} ${-half + 2},0`}
            fill={color}
          />
        </svg>
      )
    case 'hexagon': {
      const r = half - 2
      const h = r * Math.sin(Math.PI / 3)
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <polygon
            points={`-${r},0 -${r / 2},${-h} ${r / 2},${-h} ${r},0 ${r / 2},${h} -${r / 2},${h}`}
            fill={color}
          />
        </svg>
      )
    }
    case 'pill':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <rect
            x={-half + 1.5}
            y={-2.5}
            width={size - 3}
            height={5}
            rx={2.5}
            fill={color}
          />
        </svg>
      )
    case 'circle':
      return (
        <svg width={size} height={size} viewBox={viewBox} aria-hidden="true">
          <circle r={half - 2.5} fill={color} />
        </svg>
      )
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/unit-type-style.test.tsx
```

Expected: all 5 tests pass.

- [ ] **Step 5: Type-check + lint**

```bash
npm run type-check
npm run lint
```

Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add components/dashboard/org-units/unit-type-style.tsx tests/components/unit-type-style.test.tsx
git commit -m "feat(org-graph): unit-type style map and Glyph component"
```

---

## Task 3: Create `use-direction-toggle.ts`

Persists the TB/LR layout direction across reloads via `localStorage`. SSR-safe (Next.js renders the dashboard server-side first).

**Files:**
- Create: `frontend/app/components/dashboard/org-units/use-direction-toggle.ts`
- Create: `frontend/app/tests/components/use-direction-toggle.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/use-direction-toggle.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useDirectionToggle } from '@/components/dashboard/org-units/use-direction-toggle'

const KEY = 'org-graph-direction'

describe('useDirectionToggle', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    window.localStorage.clear()
  })

  it('defaults to TB when localStorage is empty', () => {
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('TB')
  })

  it('reads the persisted direction on mount', () => {
    window.localStorage.setItem(KEY, 'LR')
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('LR')
  })

  it('falls back to TB for an invalid persisted value', () => {
    window.localStorage.setItem(KEY, 'BAD')
    const { result } = renderHook(() => useDirectionToggle())
    expect(result.current[0]).toBe('TB')
  })

  it('writes to localStorage when the direction changes', () => {
    const { result } = renderHook(() => useDirectionToggle())
    act(() => {
      result.current[1]('LR')
    })
    expect(result.current[0]).toBe('LR')
    expect(window.localStorage.getItem(KEY)).toBe('LR')
  })

  it('does not throw if localStorage.setItem throws (private mode)', () => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = () => {
      throw new Error('quota')
    }
    try {
      const { result } = renderHook(() => useDirectionToggle())
      expect(() => {
        act(() => {
          result.current[1]('LR')
        })
      }).not.toThrow()
      expect(result.current[0]).toBe('LR')
    } finally {
      Storage.prototype.setItem = original
    }
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/use-direction-toggle.test.ts
```

Expected: tests fail with module-not-found.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/use-direction-toggle.ts`:

```ts
import { useEffect, useState } from 'react'

const KEY = 'org-graph-direction'

export type Direction = 'TB' | 'LR'

function readPersistedDirection(): Direction {
  if (typeof window === 'undefined') return 'TB'
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'TB' || v === 'LR' ? v : 'TB'
  } catch {
    return 'TB'
  }
}

/**
 * `localStorage`-synced state for the org-graph layout direction.
 * SSR-safe: returns 'TB' on the server; the client hydrates with the
 * persisted value (no flash because the canvas is client-only).
 */
export function useDirectionToggle(): readonly [
  Direction,
  (d: Direction) => void,
] {
  const [direction, setDirection] = useState<Direction>(readPersistedDirection)

  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, direction)
    } catch {
      // quota exceeded or private mode — keep in-memory state, do not crash.
    }
  }, [direction])

  return [direction, setDirection] as const
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/use-direction-toggle.test.ts
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/org-units/use-direction-toggle.ts tests/components/use-direction-toggle.test.ts
git commit -m "feat(org-graph): localStorage-synced direction toggle hook"
```

---

## Task 4: Create `use-dagre-layout.ts`

Pure layout pipeline. Splits into a pure function `getDagreLayout()` (testable without React) and a thin `useDagreLayout()` hook that memoizes it.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/use-dagre-layout.ts`
- Create: `frontend/app/tests/components/use-dagre-layout.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/use-dagre-layout.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { Position, type Edge, type Node } from '@xyflow/react'

import {
  getDagreLayout,
  NODE_HEIGHT,
  NODE_WIDTH,
} from '@/components/dashboard/org-units/use-dagre-layout'

function makeNode(id: string): Node<{ label: string }> {
  return {
    id,
    type: 'orgUnit',
    position: { x: 0, y: 0 },
    data: { label: id },
  }
}

function makeEdge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target }
}

describe('getDagreLayout', () => {
  it('returns an empty array for empty input', () => {
    expect(getDagreLayout([], [], 'TB')).toEqual([])
  })

  it('positions a single node and assigns TB handle positions', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out).toHaveLength(1)
    expect(out[0].position).toEqual(
      expect.objectContaining({
        x: expect.any(Number),
        y: expect.any(Number),
      }),
    )
    expect(out[0].sourcePosition).toBe(Position.Bottom)
    expect(out[0].targetPosition).toBe(Position.Top)
  })

  it('positions child below parent in TB direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'TB',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.y).toBeGreaterThan(p.position.y)
  })

  it('positions child to the right of parent in LR direction', () => {
    const out = getDagreLayout(
      [makeNode('p'), makeNode('c')],
      [makeEdge('p', 'c')],
      'LR',
    )
    const p = out.find((n) => n.id === 'p')!
    const c = out.find((n) => n.id === 'c')!
    expect(c.position.x).toBeGreaterThan(p.position.x)
    expect(c.sourcePosition).toBe(Position.Right)
    expect(c.targetPosition).toBe(Position.Left)
  })

  it('flips source/target positions when direction changes', () => {
    const tb = getDagreLayout([makeNode('a')], [], 'TB')
    const lr = getDagreLayout([makeNode('a')], [], 'LR')
    expect(tb[0].sourcePosition).toBe(Position.Bottom)
    expect(lr[0].sourcePosition).toBe(Position.Right)
  })

  it('uses the hardcoded card dimensions for layout', () => {
    expect(NODE_WIDTH).toBe(168)
    expect(NODE_HEIGHT).toBe(52)
  })

  it('preserves the original node data and type', () => {
    const out = getDagreLayout([makeNode('a')], [], 'TB')
    expect(out[0].data).toEqual({ label: 'a' })
    expect(out[0].type).toBe('orgUnit')
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/use-dagre-layout.test.ts
```

Expected: fails with module-not-found.

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/use-dagre-layout.ts`:

```ts
import dagre from '@dagrejs/dagre'
import { Position, type Edge, type Node } from '@xyflow/react'
import { useMemo } from 'react'

export const NODE_WIDTH = 168
export const NODE_HEIGHT = 52

export type Direction = 'TB' | 'LR'

/**
 * Pure layout function — no React. Runs dagre on the given graph and
 * returns the same nodes with positions and source/target handle sides
 * filled in. Easy to unit-test.
 */
export function getDagreLayout<T>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: Direction,
): Node<T>[] {
  if (nodes.length === 0) return []

  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: direction, nodesep: 28, ranksep: 64 })

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target)
  }

  dagre.layout(g)

  const isHorizontal = direction === 'LR'
  return nodes.map((n) => {
    const d = g.node(n.id)
    return {
      ...n,
      // dagre returns center-anchored coordinates; xyflow expects top-left.
      position: { x: d.x - NODE_WIDTH / 2, y: d.y - NODE_HEIGHT / 2 },
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
    }
  })
}

/**
 * Memoized React hook wrapper around `getDagreLayout`. Recomputes only
 * when nodes, edges, or direction reference-change.
 */
export function useDagreLayout<T>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: Direction,
): Node<T>[] {
  return useMemo(
    () => getDagreLayout(nodes, edges, direction),
    [nodes, edges, direction],
  )
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/use-dagre-layout.test.ts
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/org-units/use-dagre-layout.ts tests/components/use-dagre-layout.test.ts
git commit -m "feat(org-graph): pure dagre layout hook with hardcoded card dims"
```

---

## Task 5: Create `OrgUnitNode.tsx`

The card. Reads `data` provided by xyflow, exposes click + keyboard activation, and surfaces selected/on-path state via `data-state` attributes for tests.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx`
- Create: `frontend/app/tests/components/OrgUnitNode.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/OrgUnitNode.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import {
  Position,
  ReactFlowProvider,
  type NodeProps,
} from '@xyflow/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitNode } from '@/components/dashboard/org-units/OrgUnitNode'
import type { GraphNodeData } from '@/components/dashboard/org-units/OrgGraph'

function makeUnit(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    id: 'u1',
    client_id: 't1',
    parent_unit_id: null,
    name: 'Engineering',
    unit_type: 'division',
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

function renderNode(opts: {
  unit?: Partial<GraphNodeData>
  selectedId?: string | null
  onSelectPath?: Set<string>
  onSelect?: (id: string) => void
} = {}) {
  const unit = makeUnit(opts.unit)
  const onSelect = opts.onSelect ?? vi.fn()
  const data = {
    unit,
    selectedId: opts.selectedId ?? null,
    onSelectPath: opts.onSelectPath ?? new Set<string>(),
    onSelect,
  }
  const nodeProps = {
    id: unit.id,
    data,
    type: 'orgUnit',
    selected: false,
    isConnectable: false,
    xPos: 0,
    yPos: 0,
    dragging: false,
    zIndex: 0,
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
  } as unknown as NodeProps

  const utils = renderWithProviders(
    <ReactFlowProvider>
      <OrgUnitNode {...nodeProps} />
    </ReactFlowProvider>,
  )
  return { ...utils, unit, onSelect }
}

describe('OrgUnitNode', () => {
  it('renders the unit name', () => {
    renderNode()
    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('renders the type-and-member-count subtitle', () => {
    renderNode()
    expect(screen.getByText(/division\s+·\s+5 members/)).toBeInTheDocument()
  })

  it('hides the open-roles badge when openRoles is 0', () => {
    renderNode()
    expect(screen.queryByTestId('open-roles-badge')).not.toBeInTheDocument()
  })

  it('renders an amber-styled badge for openRoles 1–2', () => {
    renderNode({ unit: { openRoles: 2 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('2')
    expect(badge.className).toMatch(/amber/)
  })

  it('renders a red-styled badge for openRoles 3+', () => {
    renderNode({ unit: { openRoles: 5 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('5')
    expect(badge.className).toMatch(/red/)
  })

  it('calls onSelect with the unit id on click', () => {
    const { onSelect } = renderNode()
    fireEvent.click(screen.getByRole('button', { name: /division: Engineering/ }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('u1')
  })

  it('calls onSelect on Enter and Space keypress', () => {
    const { onSelect } = renderNode()
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    fireEvent.keyDown(card, { key: ' ' })
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('exposes data-state="selected" when selectedId matches', () => {
    renderNode({ selectedId: 'u1' })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'selected')
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })

  it('exposes data-state="on-path" when in selectedPath but not selected', () => {
    renderNode({ selectedId: 'other', onSelectPath: new Set(['u1']) })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'on-path')
  })

  it('falls back to team style and warns for an unknown unit_type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderNode({
      unit: { unit_type: 'totally_unknown' as GraphNodeData['unit_type'] },
    })
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('unknown unit_type'))
    warn.mockRestore()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/OrgUnitNode.test.tsx
```

Expected: fails with module-not-found errors for both `OrgUnitNode` and `GraphNodeData` (the latter comes from `OrgGraph`, which we haven't rewritten yet — the existing 428-LOC file already exports `GraphNodeData`, so this import resolves against the OLD file. That's deliberate: it lets us build new files without breaking the existing one.)

- [ ] **Step 3: Create the implementation**

Create `frontend/app/components/dashboard/org-units/OrgUnitNode.tsx`:

```tsx
import { memo, type CSSProperties, type KeyboardEvent } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'

import type { GraphNodeData } from './OrgGraph'
import { Glyph, getUnitTypeStyle } from './unit-type-style'

interface OrgUnitNodeData {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
}

type Pressure = 'hot' | 'steady' | null

function pressureForOpenRoles(openRoles: number): Pressure {
  if (openRoles >= 3) return 'hot'
  if (openRoles > 0) return 'steady'
  return null
}

function OrgUnitNodeImpl({
  data,
  sourcePosition = Position.Bottom,
  targetPosition = Position.Top,
}: NodeProps) {
  const { unit, selectedId, onSelectPath, onSelect } =
    data as unknown as OrgUnitNodeData
  const style = getUnitTypeStyle(unit.unit_type)

  const isSelected = selectedId === unit.id
  const isOnPath = !isSelected && onSelectPath.has(unit.id)
  const pressure = pressureForOpenRoles(unit.openRoles)

  const dataState: 'selected' | 'on-path' | 'default' = isSelected
    ? 'selected'
    : isOnPath
      ? 'on-path'
      : 'default'

  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(unit.id)
    }
  }

  const badgeClass =
    pressure === 'hot'
      ? 'bg-red-50 text-red-700 border-red-200'
      : pressure === 'steady'
        ? 'bg-amber-50 text-amber-700 border-amber-200'
        : ''

  const cardStyle: CSSProperties = {
    width: 168,
    height: 52,
    background: 'var(--px-surface)',
    borderRadius: 10,
    border: `1px solid ${
      isSelected
        ? 'var(--px-accent)'
        : isOnPath
          ? 'var(--px-accent-line)'
          : 'var(--px-hairline-strong)'
    }`,
    boxShadow: isSelected
      ? '0 0 0 3px var(--px-accent-glow)'
      : 'var(--px-shadow-sm)',
    display: 'flex',
    alignItems: 'center',
    paddingRight: 8,
    overflow: 'hidden',
    transition: 'box-shadow 120ms ease, border-color 120ms ease',
    cursor: 'pointer',
    outline: 'none',
  }

  return (
    <>
      <Handle
        type="target"
        position={targetPosition}
        style={{ opacity: 0 }}
        isConnectable={false}
      />
      <div
        role="button"
        tabIndex={0}
        aria-label={`${unit.unit_type}: ${unit.name}`}
        aria-pressed={isSelected}
        data-state={dataState}
        style={cardStyle}
        onClick={() => onSelect(unit.id)}
        onKeyDown={handleKey}
      >
        <span
          aria-hidden="true"
          style={{
            width: 4,
            alignSelf: 'stretch',
            background: style.stripVar,
            borderRadius: '10px 0 0 10px',
            marginRight: 10,
            flex: 'none',
          }}
        />
        <span
          aria-hidden="true"
          style={{
            width: 28,
            height: 28,
            borderRadius: 7,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flex: 'none',
            marginRight: 9,
            background: style.bgVar,
            border: `1px solid ${style.lineVar}`,
          }}
        >
          <Glyph kind={style.glyph} color={style.stripVar} />
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span
            style={{
              display: 'block',
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--px-fg)',
              lineHeight: 1.15,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {unit.name}
          </span>
          <span
            style={{
              display: 'block',
              fontSize: 10.5,
              color: 'var(--px-fg-3)',
              marginTop: 2,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {unit.unit_type} &middot; {unit.member_count} members
          </span>
        </span>
        {pressure && (
          <span
            data-testid="open-roles-badge"
            className={`ml-2 flex-none rounded-full border px-[7px] py-[2px] text-[10px] font-bold ${badgeClass}`}
          >
            {unit.openRoles}
          </span>
        )}
      </div>
      <Handle
        type="source"
        position={sourcePosition}
        style={{ opacity: 0 }}
        isConnectable={false}
      />
    </>
  )
}

export const OrgUnitNode = memo(OrgUnitNodeImpl)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npm run test -- tests/components/OrgUnitNode.test.tsx
```

Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/org-units/OrgUnitNode.tsx tests/components/OrgUnitNode.test.tsx
git commit -m "feat(org-graph): OrgUnitNode card component with selection states"
```

---

## Task 6: Create `OrgUnitEdge.tsx`

Custom edge component. Wraps `<BaseEdge>` from xyflow. Selected-path styling is read from edge `data`. No standalone test — exercised through the composition test in Task 7.

**Files:**
- Create: `frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx`

- [ ] **Step 1: Create the implementation**

Create `frontend/app/components/dashboard/org-units/OrgUnitEdge.tsx`:

```tsx
import {
  BaseEdge,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

interface OrgUnitEdgeData {
  selectedPath: Set<string>
}

export function OrgUnitEdge(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    source,
    target,
    data,
  } = props

  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const selectedPath = (data as unknown as OrgUnitEdgeData | undefined)
    ?.selectedPath
  const onPath =
    selectedPath?.has(source) === true && selectedPath?.has(target) === true

  return (
    <BaseEdge
      id={id}
      path={path}
      style={{
        stroke: onPath ? 'var(--px-accent)' : 'var(--px-hairline-strong)',
        strokeWidth: onPath ? 1.8 : 1.4,
        opacity: onPath ? 0.9 : 0.55,
      }}
    />
  )
}
```

- [ ] **Step 2: Type-check**

```bash
npm run type-check
```

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/org-units/OrgUnitEdge.tsx
git commit -m "feat(org-graph): OrgUnitEdge with selected-path styling"
```

---

## Task 7: Replace `OrgGraph.tsx`

The big swap. The existing 428-LOC SVG implementation is replaced in place — same default export, same named exports (`OrgLegend`, `GraphNodeData`, `Pressure`), same prop shape. `page.tsx` needs zero changes.

**Files:**
- Modify (replace contents): `frontend/app/components/dashboard/org-units/OrgGraph.tsx`
- Create: `frontend/app/tests/components/OrgGraph.test.tsx`

- [ ] **Step 1: Write the failing composition test**

Create `frontend/app/tests/components/OrgGraph.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/react'

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
    metadata: null,
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
  // jsdom has no ResizeObserver, which xyflow uses internally.
  // Polyfill with a no-op so the canvas mounts cleanly.
  if (!('ResizeObserver' in window)) {
    // @ts-expect-error injecting polyfill into the test environment
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
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
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npm run test -- tests/components/OrgGraph.test.tsx
```

Expected: fails — the existing OrgGraph.tsx doesn't render a `<button>` with the new aria-label format and doesn't have a direction toggle. Some assertions may pass coincidentally (e.g. unit names appear in the existing SVG); that's fine, the failures will guide the implementation.

- [ ] **Step 3: Replace `OrgGraph.tsx`**

Overwrite `frontend/app/components/dashboard/org-units/OrgGraph.tsx` with:

```tsx
'use client'

import { useCallback, useEffect, useMemo, useRef } from 'react'
import {
  Background,
  Controls,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeTypes,
  type OnNodesChange,
} from '@xyflow/react'

import '@xyflow/react/dist/style.css'

import type { OrgUnit } from '@/lib/api/org-units'

import { OrgUnitEdge } from './OrgUnitEdge'
import { OrgUnitNode } from './OrgUnitNode'
import { useDagreLayout } from './use-dagre-layout'
import { useDirectionToggle } from './use-direction-toggle'
import {
  Glyph,
  UNIT_TYPE_STYLE,
  type UnitType,
} from './unit-type-style'

// ─── Public types ──────────────────────────────────────────────────────────

export type Pressure = 'hot' | 'steady' | 'cool'

export interface GraphNodeData extends OrgUnit {
  /** Rolled-up open-role count for this unit and its descendants. */
  openRoles: number
  /** Coarse tier derived from `openRoles` via `pressureFor()` in page.tsx. */
  pressure: Pressure
}

interface OrgGraphProps {
  units: GraphNodeData[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  hoverId?: string | null
  /** Accepted for backward compatibility with the old SVG impl. Unused. */
  onHover?: (id: string | null) => void
}

interface OrgUnitNodeData {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
}

// `nodeTypes` and `edgeTypes` MUST be defined outside the component so
// xyflow doesn't recreate them on every render — that triggers a
// console warning and degrades performance.
const nodeTypes: NodeTypes = { orgUnit: OrgUnitNode }
const edgeTypes: EdgeTypes = { orgUnit: OrgUnitEdge }

// ─── Inner canvas component (uses useReactFlow → must be inside Provider) ──

function OrgGraphInner({ units, selectedId, onSelect }: OrgGraphProps) {
  const { fitView } = useReactFlow()
  const [direction, setDirection] = useDirectionToggle()

  // Walk parents from the selected node up to the root so the card +
  // edge components can highlight the path.
  const onSelectPath = useMemo(() => {
    const set = new Set<string>()
    if (!selectedId) return set
    const byId = new Map(units.map((u) => [u.id, u]))
    let cur: GraphNodeData | undefined = byId.get(selectedId)
    while (cur) {
      set.add(cur.id)
      cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : undefined
    }
    return set
  }, [units, selectedId])

  // Detect data corruption (multiple roots) — log once, don't crash.
  const orphanWarned = useRef(false)
  useEffect(() => {
    const roots = units.filter((u) => !u.parent_unit_id)
    if (roots.length > 1 && !orphanWarned.current) {
      console.warn(
        `OrgGraph: expected one root unit per tenant, found ${roots.length}: ${roots
          .map((r) => r.id)
          .join(', ')}`,
      )
      orphanWarned.current = true
    }
  }, [units])

  const rawNodes = useMemo<Node<OrgUnitNodeData>[]>(
    () =>
      units.map((u) => ({
        id: u.id,
        type: 'orgUnit',
        // dagre overwrites this in useDagreLayout.
        position: { x: 0, y: 0 },
        data: { unit: u, selectedId, onSelectPath, onSelect },
      })),
    [units, selectedId, onSelectPath, onSelect],
  )

  const rawEdges = useMemo<Edge[]>(
    () =>
      units
        // Defensive: drop self-loops; rest of the codebase enforces them
        // server-side, but the canvas should not infinite-loop dagre.
        .filter((u) => u.parent_unit_id && u.parent_unit_id !== u.id)
        .map((u) => ({
          id: `${u.parent_unit_id}->${u.id}`,
          source: u.parent_unit_id!,
          target: u.id,
          type: 'orgUnit',
          data: { selectedPath: onSelectPath },
        })),
    [units, onSelectPath],
  )

  const positionedNodes = useDagreLayout(rawNodes, rawEdges, direction)

  // Smoothly recenter when the user flips direction. `fitView` is
  // stable across renders per xyflow docs.
  useEffect(() => {
    fitView({ padding: 0.2, duration: 240 })
  }, [direction, fitView])

  // Controlled mode → xyflow expects a handler even though we ignore
  // changes (no drag, no edit).
  const onNodesChange: OnNodesChange = useCallback(() => {}, [])

  return (
    <ReactFlow
      nodes={positionedNodes}
      edges={rawEdges}
      onNodesChange={onNodesChange}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      // Keep the xyflow attribution visible (no Pro license) but out of
      // the way of <Controls> at bottom-right.
      attributionPosition="bottom-left"
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag
      zoomOnScroll
    >
      <Background gap={22} size={1} color="var(--px-fg-4)" />
      <Controls position="bottom-right" showInteractive={false} />
      <Panel position="top-right">
        <div
          role="group"
          aria-label="Layout direction"
          className="flex overflow-hidden rounded-md border"
          style={{
            borderColor: 'var(--px-hairline-strong)',
            background: 'var(--px-surface)',
          }}
        >
          <DirButton
            active={direction === 'TB'}
            onClick={() => setDirection('TB')}
            label="Top → Bottom"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <rect x="4" y="1" width="4" height="3" rx="0.5" fill="currentColor" />
              <rect x="4" y="8" width="4" height="3" rx="0.5" fill="currentColor" />
              <line x1="6" y1="4" x2="6" y2="8" stroke="currentColor" strokeWidth="1" />
            </svg>
          </DirButton>
          <DirButton
            active={direction === 'LR'}
            onClick={() => setDirection('LR')}
            label="Left → Right"
            borderLeft
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <rect x="1" y="4" width="3" height="4" rx="0.5" fill="currentColor" />
              <rect x="8" y="4" width="3" height="4" rx="0.5" fill="currentColor" />
              <line x1="4" y1="6" x2="8" y2="6" stroke="currentColor" strokeWidth="1" />
            </svg>
          </DirButton>
        </div>
      </Panel>
    </ReactFlow>
  )
}

function DirButton({
  active,
  onClick,
  label,
  borderLeft,
  children,
}: {
  active: boolean
  onClick: () => void
  label: string
  borderLeft?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="flex items-center gap-1.5 px-2.5 py-1 text-xs"
      style={{
        color: active ? 'var(--px-accent)' : 'var(--px-fg-2)',
        background: active ? 'var(--px-accent-tint)' : 'transparent',
        borderLeft: borderLeft ? '1px solid var(--px-hairline-strong)' : undefined,
      }}
    >
      {children}
      {label}
    </button>
  )
}

// ─── Public entry: wrap in Provider so consumers don't need to. ────────────

export function OrgGraph(props: OrgGraphProps) {
  return (
    <ReactFlowProvider>
      <OrgGraphInner {...props} />
    </ReactFlowProvider>
  )
}

// ─── Legend (still consumed from the same import path by page.tsx) ─────────

export function OrgLegend() {
  const items: { type: UnitType; label: string }[] = [
    { type: 'company', label: 'Company' },
    { type: 'client_account', label: 'Client account' },
    { type: 'region', label: 'Region' },
    { type: 'division', label: 'Division' },
    { type: 'team', label: 'Team' },
  ]
  return (
    <div
      className="flex flex-wrap gap-2.5 text-[11px]"
      style={{ color: 'var(--px-fg-3)' }}
    >
      {items.map(({ type, label }) => {
        const s = UNIT_TYPE_STYLE[type]
        return (
          <span
            key={type}
            className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <span
              className="inline-flex items-center justify-center"
              style={{
                width: 16,
                height: 16,
                borderRadius: 4,
                background: s.bgVar,
                border: `1px solid ${s.lineVar}`,
              }}
            >
              <Glyph kind={s.glyph} color={s.stripVar} size={10} />
            </span>
            <span>{label}</span>
          </span>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS**

```bash
npm run test -- tests/components/OrgGraph.test.tsx
```

Expected: all 6 tests pass.

- [ ] **Step 5: Run the full test suite**

```bash
npm run test
```

Expected: zero new failures. (The existing `org-units-client-account-flow.test.tsx` and any other org-units-touching tests must still pass — they don't depend on OrgGraph internals.)

- [ ] **Step 6: Type-check + lint**

```bash
npm run type-check
npm run lint
```

Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/org-units/OrgGraph.tsx tests/components/OrgGraph.test.tsx
git commit -m "feat(org-graph): replace SVG OrgGraph with xyflow + dagre

Drop the 428-LOC hand-rolled SVG canvas in favor of @xyflow/react +
@dagrejs/dagre. Per-type colors and shapes via unit-type-style;
direction toggle (TB/LR) persisted to localStorage; pan/zoom/fitView
courtesy of xyflow. Public exports unchanged — page.tsx untouched."
```

---

## Task 8: Final verification

Belt-and-suspenders: full type-check, lint, build, and a manual smoke pass on the running dashboard.

**Files:** none modified (verification only)

- [ ] **Step 1: Production build**

```bash
cd frontend/app
npm run build
```

Expected: clean build, no TypeScript errors, no ESLint errors. Pay attention to bundle size — xyflow + dagre add ~150 KB gzipped.

- [ ] **Step 2: Manual smoke at `http://localhost:3001/settings/org-units`**

Run the dev server (or rely on the user's existing one):

```bash
npm run dev
```

Then verify (check off each item; if any fails, fix before merging):

- [ ] Tree renders — the BinQle root card is visible.
- [ ] Click a node → side panel below the canvas updates with that unit's detail (preserves the existing `selectedId` contract through `page.tsx`).
- [ ] Pan + zoom work (drag the background, scroll-wheel zoom, the bottom-right Controls fit-view button recenters).
- [ ] Direction toggle (top-right Panel) flips between TB and LR; layout reflows, fit-view animation runs.
- [ ] Reload the page → the previously chosen direction is still active.
- [ ] Hover a card → border tints to accent line, shadow lifts.
- [ ] Click a card → selected styling applies (accent border + glow ring); ancestor cards get the on-path subtle styling.
- [ ] Open-roles badge: hidden for units with 0 open roles; amber for 1–2; red for 3+.
- [ ] Create a new sub-unit through the existing form on the same page → new card appears with the right glyph + color for its type.

If a tenant exists with a `client_account`, also verify the diamond glyph + purple ramp render correctly. (For BinQle, this isn't applicable since `clients.workspace_mode` is the direct-hire mode.)

- [ ] **Step 3: Commit any tweaks discovered during smoke**

If the smoke pass surfaced spacing, color, or accessibility tweaks, commit them as a separate small commit:

```bash
git add components/dashboard/org-units/
git commit -m "fix(org-graph): post-smoke polish — <one-line summary>"
```

If no tweaks needed, skip this step.

- [ ] **Step 4: Final status check**

```bash
git log --oneline -8
git status
```

Expected: clean tree, ~7-8 new commits all under `feat(org-graph)` / `test(org-graph)` / `fix(org-graph)`.

---

## Self-Review Checklist (already run)

- **Spec coverage:** Every decision (D1–D16) maps to a task. D1 (interactivity = read-only) shaped `nodesDraggable={false}` etc. in Task 7. D3/D4 (color + glyph map) → Task 2. D5/D6 (background, controls) → Task 7. D7 (no minimap) → Task 7 omits `<MiniMap>`. D8/D9 (direction toggle UI + persistence) → Tasks 3 + 7. D10 (fitView triggers) → Task 7 `useEffect([direction])`. D11 (selected-path edges) → Task 6 + Task 7. D12 (badge replaces ring) → Task 5. D13 (controlled mode) → Task 7. D14 (hardcoded dims) → Task 4. D15 (preserve prop shape) → Task 7's `OrgGraphProps`. D16 (CLAUDE.md fix) → automatic when this lands.
- **Placeholder scan:** No "TBD", "TODO", or "implement later" left in the plan.
- **Type consistency:** `Direction` type defined consistently in `use-direction-toggle.ts` and `use-dagre-layout.ts`. `OrgUnitNodeData` shape matches between `OrgUnitNode.tsx` and `OrgGraph.tsx` (both use `{ unit, selectedId, onSelectPath, onSelect }`). `GraphNodeData` is exported from `OrgGraph.tsx` and imported by `OrgUnitNode.tsx` via `import type` (TS-only, no runtime cycle). `NODE_WIDTH`/`NODE_HEIGHT` constants live in `use-dagre-layout.ts` and are used nowhere else (the card hardcodes `168` / `52` literally — keeps the visual dimensions co-located with the visual code).
