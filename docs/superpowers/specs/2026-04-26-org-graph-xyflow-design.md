# Org Graph — Replace Hand-Rolled SVG with xyflow + dagre

**Date:** 2026-04-26
**Status:** Approved (design phase)
**Owner:** Ishant Pundir
**Scope:** Replace `frontend/app/components/dashboard/org-units/OrgGraph.tsx` (428 LOC, hand-rolled SVG, fixed viewBox, depth-keyed shapes) with a `@xyflow/react` + `@dagrejs/dagre` implementation. Per-type color + shape coding, configurable layout direction (TB / LR), pan / zoom / fitView, and full design-system integration. Frontend only — no backend changes.

---

## 1. Goals

1. Drop the hand-rolled SVG canvas in favor of an industry-standard graph library (`@xyflow/react`) so future enhancements (drag, edit, minimap, etc.) come from upstream rather than custom code.
2. Use `@dagrejs/dagre` for automatic hierarchical layout, exposing a user-facing direction toggle (top-to-bottom / left-to-right) instead of the current single-axis fixed layout.
3. Encode each org-unit type with its own shape **and** color (current implementation keys shapes off **depth**, which is wrong — a `division` at depth 1 looks the same as a `team` at depth 2).
4. Match the existing dashboard design language exactly: warm-cream `--px-bg`, off-white `--px-surface` cards, deep-teal `--px-accent`, hairline borders, `--px-shadow-sm`. No new color tokens.
5. Preserve the existing public API of `<OrgGraph>` (default export + named `OrgLegend`, `GraphNodeData`, `Pressure`) so `app/(dashboard)/settings/org-units/page.tsx` keeps working with **zero** changes — same path, same exports, same prop shape.

## 2. Non-Goals

- **No mutation of the org tree.** Reparenting, deleting, renaming, or creating units from the canvas is out of scope (Phase C, separate spec).
- **No drag-to-reposition.** Read-only view; auto-layout is canonical. (Free-drag was explicitly considered and rejected during brainstorm.)
- **No minimap.** Org trees rarely exceed 30 nodes; minimap is visual noise here.
- **No backend changes.** `OrgUnit` shape, `useOrgUnits()` hook, `/api/org-units/*` endpoints — all untouched.
- **No new tests for xyflow internals.** Pan/zoom/fitView are the library's responsibility.
- **No animation overhaul.** xyflow's defaults plus a 240ms `fitView` ease are enough.

## 3. Decisions Locked

| ID | Decision | Pick |
|---|---|---|
| D1 | Interactivity scope | **A** — click-to-select + pan/zoom + fit-view. Read-only exploration, no drag, no reparent. |
| D2 | Visual direction | **B** — uniform card with type-color side strip + type-color shape glyph. Card body shows name + `type · N members` subtitle + open-roles badge. |
| D3 | Color mapping per unit type | `company → blue-600/blue-50`, `client_account → purple-600/purple-50`, `region → amber-700/amber-50`, `division → --px-accent-2/green-50`, `team → --px-fg/--px-bg-2`. All resolve to the warm-light palette via the `@theme` remap in `globals.css`. |
| D4 | Glyph mapping per unit type | `company → square`, `client_account → diamond`, `region → hexagon`, `division → pill`, `team → circle`. |
| D5 | Background | xyflow `<Background>` with dot pattern, low-opacity `--px-fg-4`. On. |
| D6 | Controls | xyflow `<Controls>`, `position="bottom-right"`. On. Restyled to `--px-surface` / `--px-hairline-strong` / `--px-fg-2`. |
| D7 | MiniMap | Off. |
| D8 | Direction toggle UI | `<Panel position="top-right">` with a 2-button segmented control (`TB` / `LR`), default `TB`. `aria-pressed` reflects active direction. |
| D9 | Direction persistence | `localStorage` key `org-graph-direction`, value `'TB' \| 'LR'`. SSR-safe read with parse guard, defaults to `'TB'`. |
| D10 | fitView triggers | (a) Initial mount via `<ReactFlow fitView />`. (b) On direction change, call `useReactFlow().fitView({ padding: 0.2, duration: 240 })`. (c) **Not** on data change — would yank the user's pan/zoom mid-task. |
| D11 | Selected-path highlight | Keep. Edges with both endpoints in `onSelectPath` render in `--px-accent` at full opacity; others render at `--px-hairline-strong` muted. |
| D12 | Pressure ring (current concept) | Drop the standalone ring. Surface the same hot/steady/cool signal via the open-roles badge color: `≥3 → red-* (px-danger-ish)`, `1-2 → amber-* (px-caution-ish)`, `0 → hidden`. |
| D13 | State management pattern | **Approach A — derived state, controlled xyflow.** `useOrgUnits()` is canonical; a `useMemo` derives raw nodes/edges; `useDagreLayout()` returns positioned nodes; xyflow renders in controlled mode with `onNodesChange` as a no-op. |
| D14 | Node dimensions for dagre | Hardcoded `NODE_WIDTH=168`, `NODE_HEIGHT=52`. Cards are fixed-size by design — keeps dagre deterministic and avoids measure-then-relayout flicker. |
| D15 | Selection contract with parent page | Unchanged. `<OrgGraph units selectedId onSelect />` keeps its existing prop shape. `onHover` becomes optional (xyflow handles hover internally). |
| D16 | Stale `CLAUDE.md` line about `OrgUnitCanvas.tsx` | Becomes accurate after this lands — no separate doc fix. |

## 4. Architecture

### 4.1 Dependencies

Frontend only. Add to `frontend/app/package.json`:

```json
"@xyflow/react": "^12",
"@dagrejs/dagre": "^1"
```

`@xyflow/react/dist/style.css` is imported once at the top of `OrgGraph.tsx`. xyflow scopes its own classes (`xy-*`, `react-flow__*`); no conflicts with `--px-*`. The handful of `xy-theme__*` defaults that bleed through (notably the `<Controls>` button colors) are overridden in a small CSS module to match the dashboard palette.

### 4.2 File Layout

Under `frontend/app/components/dashboard/org-units/`:

| File | Responsibility | Approx. LOC |
|---|---|---|
| `OrgGraph.tsx` | Public entry. Wraps `<ReactFlowProvider>`, renders `<ReactFlow>` + `<Background>` + `<Controls>` + `<Panel>` (direction toggle). Wires data → layout → render. Same export name and prop shape as the existing file. | ~120 |
| `OrgUnitNode.tsx` | Custom xyflow node component. Reads `data.unit` (which is `GraphNodeData` and carries `openRoles` + `pressure`), `data.selectedId`, `data.onSelectPath`, `data.onSelect`. Renders strip + glyph + name + subtitle + badge. `React.memo`'d. | ~90 |
| `OrgUnitEdge.tsx` | Custom edge wrapping `<BezierEdge>`. Reads `selectedPath` from edge `data` to switch between accent and hairline styling. | ~30 |
| `unit-type-style.ts` | Single source of truth: `UNIT_TYPE_STYLE: Record<UnitType, { stripVar, bgVar, lineVar, glyph }>` plus a `Glyph` component that renders the SVG shape inline given a glyph kind + color. | ~70 |
| `use-dagre-layout.ts` | Pure hook: `(nodes, edges, direction) → positionedNodes`. Memoized on `(nodes, edges, direction)`. No xyflow-specific imports — only types. Easy to unit-test. | ~50 |
| `use-direction-toggle.ts` | `useState<'TB' \| 'LR'>` synchronized to `localStorage('org-graph-direction')` with parse guard. SSR-safe. | ~25 |

### 4.3 Files Removed / Changed

- `OrgGraph.tsx` (existing 428-LOC SVG implementation) is **rewritten in place** — not renamed. Same default export, same prop shape, so `app/(dashboard)/settings/org-units/page.tsx:427` keeps working unmodified.
- The `OrgLegend` named export is **folded into** `OrgGraph.tsx` (still exported from the same module, still consumed by `page.tsx`). Internally it uses `UNIT_TYPE_STYLE` so the legend can never drift from the node colors.
- The `GraphNodeData` and `Pressure` named exports are preserved (still consumed by `page.tsx` for the rolled-up open-roles + pressure derivation).

## 5. Data Flow

```
useOrgUnits()                         ← TanStack Query, canonical
   │
   ▼
useMemo: rawNodes, rawEdges           ← pure derivation
   │
   ▼
useDagreLayout(rawNodes, edges, dir)  ← runs dagre, returns positioned nodes
   │
   ▼
<ReactFlow nodes={positioned} edges={edges} ... />
   │
   ▼
<OrgUnitNode data={...} />            ← reads selectedId, calls onSelect on click
```

Selection lives in the **page**, not in xyflow internal state. `selectedId: string | null` is passed down; `onSelect: (id: string) => void` is passed up. Same contract as today.

### 5.1 Public Prop Shape (unchanged from today)

```ts
// Re-exported from OrgGraph.tsx — same signatures as the file being replaced.
export type Pressure = 'hot' | 'steady' | 'cool'
export interface GraphNodeData extends OrgUnit {
  openRoles: number   // rolled-up count, computed by page.tsx
  pressure: Pressure  // derived from openRoles via existing pressureFor() heuristic
}

interface OrgGraphProps {
  units: GraphNodeData[]
  selectedId: string | null
  onSelect: (id: string) => void
  onHover?: (id: string | null) => void   // now optional — xyflow handles hover
}
```

`page.tsx` already computes `rolledOpenRoles` and constructs `GraphNodeData[]` before passing to `<OrgGraph>` — that logic is preserved verbatim.

### 5.2 Internal Node `data` Shape

```ts
type OrgUnitNodeData = {
  unit: GraphNodeData            // includes name, type, member_count, openRoles, pressure
  selectedId: string | null
  onSelectPath: Set<string>      // ancestor ids of selectedId, precomputed
  onSelect: (id: string) => void
}
```

`onSelectPath` is computed in a `useMemo` at the canvas level by walking parents up from `selectedId`. Both `OrgUnitNode` and `OrgUnitEdge` consult it for path-highlight styling. No prop drilling beyond `data`.

### 5.3 Raw Node + Edge Construction

```ts
const rawNodes = useMemo<Node<OrgUnitNodeData>[]>(
  () => units.map((u) => ({
    id: u.id,
    type: 'orgUnit',
    position: { x: 0, y: 0 },        // overwritten by useDagreLayout
    data: { unit: u, selectedId, onSelectPath, onSelect },
  })),
  [units, selectedId, onSelectPath, onSelect],
)

const rawEdges = useMemo<Edge[]>(
  () => units
    .filter((u) => u.parent_unit_id && u.parent_unit_id !== u.id)   // defensive cycle filter
    .map((u) => ({
      id: `${u.parent_unit_id}->${u.id}`,
      source: u.parent_unit_id!,
      target: u.id,
      type: 'orgUnit',
      data: { selectedPath: onSelectPath },
    })),
  [units, onSelectPath],
)
```

## 6. Layout Pipeline

```ts
// use-dagre-layout.ts
import dagre from '@dagrejs/dagre'
import { Position, type Node, type Edge } from '@xyflow/react'
import { useMemo } from 'react'

export const NODE_WIDTH = 168
export const NODE_HEIGHT = 52

export function useDagreLayout<T>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: 'TB' | 'LR',
): Node<T>[] {
  return useMemo(() => {
    const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
    g.setGraph({ rankdir: direction, nodesep: 28, ranksep: 64 })

    nodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }))
    edges.forEach((e) => g.setEdge(e.source, e.target))
    dagre.layout(g)

    const isHorizontal = direction === 'LR'
    return nodes.map((n) => {
      const d = g.node(n.id)
      return {
        ...n,
        position: { x: d.x - NODE_WIDTH / 2, y: d.y - NODE_HEIGHT / 2 },
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
      }
    })
  }, [nodes, edges, direction])
}
```

`nodesep: 28` (sibling gap) and `ranksep: 64` (level gap) are tuned for 168×52 cards — they keep edges short and cards from crowding without tab-character precision.

## 7. Visual System

### 7.1 Per-Type Style Map (`unit-type-style.ts`)

```ts
type UnitType = 'company' | 'client_account' | 'region' | 'division' | 'team'
type GlyphKind = 'square' | 'diamond' | 'hexagon' | 'pill' | 'circle'

export const UNIT_TYPE_STYLE: Record<UnitType, {
  stripVar: string   // CSS var for the 4px side strip + glyph fill
  bgVar: string      // CSS var for the glyph plate background
  lineVar: string    // CSS var for the glyph plate border
  glyph: GlyphKind
}> = {
  company:        { stripVar: 'var(--color-blue-600)',   bgVar: 'var(--color-blue-50)',   lineVar: 'var(--color-blue-200)',   glyph: 'square'  },
  client_account: { stripVar: 'var(--color-purple-600)', bgVar: 'var(--color-purple-50)', lineVar: 'var(--color-purple-100)', glyph: 'diamond' },
  region:         { stripVar: 'var(--color-amber-700)',  bgVar: 'var(--color-amber-50)',  lineVar: 'var(--color-amber-100)',  glyph: 'hexagon' },
  division:       { stripVar: 'var(--px-accent-2)',      bgVar: 'var(--color-green-50)',  lineVar: 'var(--color-green-100)',  glyph: 'pill'    },
  team:           { stripVar: 'var(--px-fg)',            bgVar: 'var(--px-bg-2)',         lineVar: 'var(--px-hairline-strong)', glyph: 'circle' },
} as const

export function Glyph({ kind, color, size = 14 }: { kind: GlyphKind; color: string; size?: number }) {
  // returns inline SVG <rect>/<polygon>/<circle> filled with `color`.
}
```

Every value is an existing token. The `bg-blue-50 / text-blue-700` Tailwind utilities resolve to the same warm-light remapped palette via `@theme` in `globals.css`, so consumers can mix utility classes (for hover transitions, etc.) with the `var()` references (for the strip / glyph fill, where utilities can't reach).

### 7.2 Card Chrome

- Dimensions: `168 × 52`
- Background: `var(--px-surface)`
- Border: `1px solid var(--px-hairline-strong)`, `border-radius: 10px`
- Shadow: `var(--px-shadow-sm)`
- Internal layout, left to right:
  - 4px-wide vertical strip in `stripVar`
  - 28×28 glyph plate in `bgVar` with `1px solid lineVar` border, 7px radius — contains the shape SVG filled with `stripVar`
  - Body: name (13px, 600 weight, `--px-fg`, single-line ellipsis) + subtitle (10.5px, `--px-fg-3`, `${type} · ${member_count} members`)
  - Open-roles badge (right side, only when `openRoles > 0`) — see 7.4

### 7.3 Interaction States

| State | Border | Shadow | Notes |
|---|---|---|---|
| Default | `--px-hairline-strong` | `--px-shadow-sm` | — |
| Hover | `--px-accent-line` | `0 4px 14px rgba(58, 45, 28, 0.10)` | Transition 120ms ease |
| Selected | `--px-accent` | `0 0 0 3px var(--px-accent-glow)` (replaces shadow) | `aria-pressed="true"` |
| On selected-path (not selected) | `--px-accent-line` | default | Subtle, like hover-but-static |

### 7.4 Open-Roles Badge

```
openRoles === 0  → not rendered
openRoles in [1,2] → bg-amber-50 text-amber-700 border-amber-200   (pressure: steady)
openRoles >= 3      → bg-red-50   text-red-700   border-red-200    (pressure: hot)
```

Same `pressureFor()` heuristic as today, just a different surface. The standalone pressure ring concept is dropped (D12).

### 7.5 Edge Styling (`OrgUnitEdge.tsx`)

| Case | Color | Width | Opacity |
|---|---|---|---|
| Both endpoints in `selectedPath` | `var(--px-accent)` | 1.8 | 0.9 |
| Otherwise | `var(--px-hairline-strong)` | 1.4 | 0.55 |

Wraps `<BezierEdge>` from xyflow — we don't reimplement the path math, just style it.

### 7.6 Direction Toggle Panel

`<Panel position="top-right">` containing a 2-button segmented control:

```
[ ⊟ Top → Bottom | ⊞ Left → Right ]
```

- Buttons match the `Button` outline variant, joined into a single rounded group.
- Icons are inline SVG. Labels are visible (no icon-only).
- `aria-pressed` reflects the active direction.
- Keyboard: arrow-left/right cycles between options when focused.

## 8. Persistence (`use-direction-toggle.ts`)

```ts
const KEY = 'org-graph-direction'

function readPersistedDirection(): 'TB' | 'LR' {
  if (typeof window === 'undefined') return 'TB'
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'TB' || v === 'LR' ? v : 'TB'
  } catch {
    return 'TB'
  }
}

export function useDirectionToggle() {
  const [direction, setDirection] = useState<'TB' | 'LR'>(readPersistedDirection)

  useEffect(() => {
    try { window.localStorage.setItem(KEY, direction) } catch { /* quota exceeded → silent */ }
  }, [direction])

  return [direction, setDirection] as const
}
```

The function-init form of `useState` runs once on mount; SSR returns `'TB'` and the client hydrates with the persisted value. No flash because the canvas only renders after hydration anyway.

## 9. Edge Cases

| Case | Behavior |
|---|---|
| `useOrgUnits()` is loading | Page-level skeleton already exists. `<OrgGraph>` is not rendered until data is present. |
| Single root unit (just the company) | One card, centered, no edges. dagre handles single-node graphs. `fitView` zooms to a sensible level. |
| Multiple roots (data corruption) | Server-side invariant guarantees one `company` per tenant, but if violated, dagre lays out a forest. Code emits one `console.warn` listing orphan ids. |
| Very deep trees (>5 levels) | dagre handles arbitrary depth. `ranksep: 64` keeps spacing readable. Pan/zoom + fitView do the rest. |
| Self-parent or cycle | Server-side invariant. Defensive: `useMemo` for `rawEdges` filters `source === target`. |
| Type unknown to `UNIT_TYPE_STYLE` | Falls back to the `team` style + `console.warn(\`OrgGraph: unknown unit_type \${t}\`)`. Future-proofs against backend adding a 6th type before frontend updates. |
| User has stale `localStorage` value (e.g. `'BT'`) | Parse guard returns `'TB'`. |
| User in private mode where `localStorage` is blocked | Try/catch swallows; component runs with in-memory state only. |

## 10. Accessibility

- `OrgUnitNode` root has `role="button"`, `aria-label={\`${unit.unit_type}: ${unit.name}\`}`, `aria-pressed={isSelected}`. Click + Enter + Space all trigger `onSelect`.
- The type-color strip and glyph are decorative (`aria-hidden="true"`). The unit type is also in the visible subtitle, so colorblind users don't lose information.
- Direction toggle: native `<button>` elements with `aria-pressed`. xyflow's keyboard support (arrow-key panning, `+`/`-` zoom) is on by default and we don't disable it.
- `<Controls>` buttons get accessible names from xyflow's defaults; we keep the defaults.
- Focus ring on `OrgUnitNode` uses `--px-accent` outline at 2px to match the rest of the dashboard.

## 11. Security

No new attack surface. Component reads `OrgUnit` data already fetched through the audited `apiFetch` + RLS pipeline. No HTML injection: `unit.name` and other strings render as text content (React default escaping). No `dangerouslySetInnerHTML`.

## 12. Testing

Vitest + RTL, pattern from `tests/components/org-units-client-account-flow.test.tsx`.

### 12.1 `use-dagre-layout.test.ts` — pure unit tests

- Single-node input → returned position centered, no errors.
- Two-level tree, direction `'TB'` → child's `y` is greater than parent's `y`; both share `x` (or close to it).
- Two-level tree, direction `'LR'` → child's `x` is greater; both share `y`.
- Direction flip → returned `sourcePosition`/`targetPosition` swap correctly.
- Cycle in input edges → layout still produces (cycle is filtered upstream in `rawEdges`, but the hook itself is tested with non-cyclic input).
- Empty `nodes` and `edges` → returns `[]`.

### 12.2 `OrgUnitNode.test.tsx` — card in isolation

Wrap in `<ReactFlowProvider>` for `useReactFlow()` no-op compatibility.

- Renders name + member count + type subtitle from `data.unit`.
- Open-roles badge: hidden at `openRoles=0`, amber styling at `2`, red styling at `5`.
- Click → calls `data.onSelect(unit.id)` exactly once with the unit id.
- `selectedId === unit.id` → root has `data-state="selected"` (we expose this attribute for testability).
- `onSelectPath.has(unit.id)` and not selected → root has `data-state="on-path"`.
- Unknown `unit_type` → renders without crash, console warning fires (assert via spy on `console.warn`).

### 12.3 `OrgGraph.test.tsx` — composition

- Renders `n` cards for `n` units. Edges count equals `units.filter(u => u.parent_unit_id).length`.
- Direction toggle button click → `localStorage.getItem('org-graph-direction')` returns the new value; one of the rendered cards reflects the new `sourcePosition` (assert via DOM data attributes xyflow exposes).
- Selecting a node propagates: `onSelect` callback prop is called with the clicked id.
- Stale `localStorage` value (`'BAD'`) → component renders with `'TB'`.

### 12.4 Out of Scope for Tests

- xyflow internals: pan, zoom, fitView, viewport math.
- dagre internals: actual coordinate values beyond relative ordering.

## 13. Verification Before Merging

Run from `frontend/app/`:

```bash
npm run lint              # zero errors
npm run type-check        # zero errors
npm run test -- org-units # all green
npm run build             # clean
```

Manual smoke at `http://localhost:3001/settings/org-units` on the BinQle tenant:

- [ ] Tree renders with the BinQle root card visible.
- [ ] Click a node → side panel updates with that unit (existing behavior preserved).
- [ ] Direction toggle works; reload preserves choice.
- [ ] Pan + zoom work; `<Controls>` fit-view button recenters.
- [ ] Hover + selected states match the v2 mockup.
- [ ] After creating a new region / division / team via the existing form, the new card appears with the right glyph and color.

If a tenant with a `client_account` is available, verify its diamond glyph + purple ramp render correctly. (No automated screenshot regression — visual confirmation is enough at this stage.)

## 14. Open Questions

None. All decisions in §3 ratified during the 2026-04-26 brainstorm.

## 15. References

- xyflow v12 docs — Custom Nodes: https://reactflow.dev/examples/nodes/custom-node
- xyflow v12 docs — Dagre Tree Layout: https://reactflow.dev/examples/layout/dagre
- `@dagrejs/dagre` — Graph layout API: https://github.com/dagrejs/dagre/wiki
- Existing OrgGraph.tsx (428 LOC, to be replaced): `frontend/app/components/dashboard/org-units/OrgGraph.tsx`
- Consuming page (no changes): `frontend/app/app/(dashboard)/settings/org-units/page.tsx:427`
- Design tokens: `frontend/app/app/globals.css` (`--px-*`, remapped Tailwind ramps)
- Companion mockup (visual companion v2): `.superpowers/brainstorm/<session>/content/node-style-v2.html`
