# Org Graph — Right-Click Radial Context Menu

**Date:** 2026-04-26
**Status:** Approved (design phase)
**Owner:** Ishant Pundir
**Scope:** Add a right-click radial ("spider web") context menu to the new xyflow-based `OrgGraph`. Lets the user delete a unit or create a child of any valid type without leaving the canvas. Frontend only — uses existing `POST /api/org-units` and `DELETE /api/org-units/{id}` endpoints unchanged.

---

## 1. Goals

1. Make the most common destructive/creative actions on a unit one gesture away from its node — no detour through the side panel or modal forms.
2. Enforce nesting rules at the menu level: only show child types that are legal for the right-clicked parent. The backend re-validates as a safety net.
3. Match the existing dashboard design language: `--px-*` tokens, lucide icons, accent-color spokes, soft shadows.
4. Be keyboard-accessible: the menu opens via Shift+F10 / Menu key on a focused node and is fully navigable without a mouse.
5. Mutations flow through existing TanStack Query hooks (`useCreateOrgUnit`, `useDeleteOrgUnit`) so cache invalidation matches what the rest of the page expects.

## 2. Non-Goals

- **No backend changes.** Endpoints exist; nesting rules are already enforced server-side.
- **No new mutation surfaces.** Delete still hits the same endpoint; create still posts the same payload.
- **No "rename" or "move" in this menu.** Rename lives in the side panel; move (re-parent) is a separate feature with its own design (drag-to-reparent, future scope).
- **No bulk operations** (multi-select + delete-many). Single-unit only.
- **No mobile / long-press.** Dashboard is desktop-only per `frontend/app/CLAUDE.md`.
- **No rich confirmation patterns** (typed-name gate, undo toast). Single-step `DangerConfirmDialog` is enough; the typed-name flow is for tenant-nuke at admin level.

## 3. Decisions Locked

| ID | Decision | Pick |
|---|---|---|
| D1 | Visual style | **Variant B + labels** — pill items (icon + name) at the end of thin accent-color spokes radiating from the right-click pivot point. |
| D2 | Menu items per unit | Delete (red, destructive) **plus** one item per legal child type. Items show lucide icon + type name. Non-deletable units (`admin_delete_disabled === true`, e.g. company root) omit the Delete item. |
| D3 | Add-child flow | **Inline mini-form at the pivot point.** Click a child-type item → the radial collapses, an inline pill appears at the same point with a name input + Enter-to-create / Esc-to-cancel. Submits to `POST /api/org-units` via `useCreateOrgUnit()`. |
| D4 | Animation | **Pure CSS keyframes**, no new dep. Spokes fade in (`opacity 0 → 0.55`); pills scale (`0 → 1`) with 30 ms stagger; ~250 ms total. Spokes draw from pivot outward. Closing animations mirror in reverse with shorter duration (~150 ms). |
| D5 | Keyboard a11y | When a card has focus, **Shift+F10** or the **Menu** key opens the radial. Inside the menu: ArrowLeft/ArrowRight (or ArrowUp/ArrowDown) cycle items, Enter activates, Escape closes. Focus trap inside the open menu; restored to the originating card on close. |
| D6 | Delete confirmation | Single-step `DangerConfirmDialog` ("Delete *<unit name>* and all its sub-units? This cannot be undone."). Confirm button uses the destructive variant. No typed-name gate. |
| D7 | State ownership | Menu open/closed state lives in `OrgGraph` (UI state of the graph). Mutation handlers `onDelete` and `onCreateChild` are new props on `<OrgGraph>` so the page wires them to existing TanStack Query hooks. |
| D8 | Right-click also selects | Right-clicking a node sets `selectedId` to that node before opening the menu — same as left-click. The side panel stays in sync with whatever the menu is acting on. |
| D9 | Position & viewport clipping | Menu is positioned at the right-click event's `clientX`/`clientY`. If any item would render outside the canvas viewport, the entire menu translates inward by the smallest offset that fits all items. No per-item flipping. |
| D10 | Workspace mode filter | Show `client_account` in the menu unconditionally (matching the existing `UNIT_TYPES` list in `page.tsx`). Backend rejects with a 400 if the tenant's `workspace_mode !== 'agency'`; the rejection bubbles up as a toast. |

## 4. Architecture

### 4.1 New Files

Under `frontend/app/components/dashboard/org-units/`:

| File | Responsibility | Approx. LOC |
|---|---|---|
| `OrgUnitContextMenu.tsx` | The radial menu component. Renders pivot dot, spokes, and pill items at computed angles. Owns the open/close animation, keyboard handling, viewport clipping. Pure presentation — no data-fetching or mutations. | ~180 |
| `OrgUnitInlineCreate.tsx` | The inline mini-form that appears at the pivot after a child-type pick. Renders the type chip, name input, and submit/cancel hints. Calls back to a parent-supplied `onSubmit(name)`. | ~70 |
| `unit-children-rules.ts` | Pure helper: `getAllowedChildTypes(parent: UnitType): UnitType[]`. Encodes the same nesting rules the backend enforces so the menu can filter items locally. Easy to unit-test. | ~25 |

### 4.2 Modified Files

| File | Change |
|---|---|
| `components/dashboard/org-units/OrgGraph.tsx` | Add `onDelete?: (id: string) => void` and `onCreateChild?: (parentId: string, unit_type: UnitType, name: string) => Promise<void>` props. Wire `onNodeContextMenu` on `<ReactFlow>` to open the menu. Render `<OrgUnitContextMenu>` and `<OrgUnitInlineCreate>` overlay. |
| `app/(dashboard)/settings/org-units/page.tsx` | Pass new `onDelete` and `onCreateChild` props to `<OrgGraph>` wired to `useDeleteOrgUnit()` and `useCreateOrgUnit()`. Render `<DangerConfirmDialog>` for the delete confirmation. |
| `components/dashboard/org-units/OrgUnitNode.tsx` | Add `onKeyDown` handler for Shift+F10 / Menu key. Forwards an "open context menu" event to the canvas via a callback in `data` (similar to how `onSelect` flows today). |

### 4.3 Files Untouched

- Backend (`backend/nexus/app/modules/org_units/`) — endpoints already exist; nesting rules already enforced.
- `lib/hooks/use-create-org-unit.ts`, `use-delete-org-unit.ts` — already wired with cache invalidation.
- `lib/api/org-units.ts` — `create()` and `delete()` already typed.

## 5. Component Tree

```
<OrgGraph>
  <ReactFlowProvider>
    <OrgGraphInner>
      <ReactFlow onNodeContextMenu={openMenu}>
        <Background /> <Controls /> <Panel /> ... existing children
      </ReactFlow>

      {menuTarget && (
        <OrgUnitContextMenu
          target={menuTarget}              // { unit, x, y }
          onClose={() => setMenuTarget(null)}
          onPickDelete={() => onDelete?.(menuTarget.unit.id)}
          onPickChild={(type) => setInlineCreate({ type, x, y })}
        />
      )}

      {inlineCreate && (
        <OrgUnitInlineCreate
          parentId={menuTarget.unit.id}
          unitType={inlineCreate.type}
          x={inlineCreate.x}
          y={inlineCreate.y}
          onSubmit={(name) => onCreateChild?.(parentId, type, name)}
          onCancel={() => setInlineCreate(null)}
        />
      )}
    </OrgGraphInner>
  </ReactFlowProvider>
</OrgGraph>
```

`menuTarget` and `inlineCreate` are mutually exclusive states: opening the inline create closes the menu.

## 6. Nesting Rules (`unit-children-rules.ts`)

Mirrors `backend/nexus/app/modules/org_units/service.py::create_org_unit`:

```ts
const ALL_CHILD_TYPES: UnitType[] = ['region', 'division', 'client_account', 'team']

export function getAllowedChildTypes(parent: UnitType): UnitType[] {
  if (parent === 'team') return []                          // teams are leaves
  if (parent === 'client_account') {
    return ALL_CHILD_TYPES.filter((t) => t !== 'client_account')
  }
  return ALL_CHILD_TYPES                                    // company / region / division
}
```

`company` is never a child of anything (root-only), so it never appears in this list.

| Right-clicked unit | Menu items (in this order) |
|---|---|
| `company` | Region, Division, Client account, Team — *no Delete* |
| `client_account` | Delete, Region, Division, Team |
| `region` | Delete, Region, Division, Client account, Team |
| `division` | Delete, Region, Division, Client account, Team |
| `team` | Delete only |

Item order is fixed: Delete first (top), then child types in `[Region, Division, Client account, Team]` order. Items are equally-spaced around the pivot (full 360° fan) starting from 12 o'clock and going clockwise.

## 7. Geometry

- **Radius:** 110 px (pill width fits without colliding spokes for 5 items at 72° apart).
- **Pill height:** 36 px, padding `0 14px 0 10px`, `border-radius: 999px`.
- **Spoke:** thin `1.4 px` accent line from pivot to the inner edge of the pill (not all the way to the pill center — looks cleaner).
- **Pivot dot:** 8 px solid `--px-accent` with a 6 px `--px-accent-tint` halo.

For 1-item case (team's Delete-only): single pill is positioned at 12 o'clock; one spoke; the visual is still a "spoke" rather than collapsing to a non-radial popover.

For viewport clipping (D9): after the menu's bounding box is computed, if any edge falls outside the canvas, translate the entire group by `(dx, dy)` toward the canvas center. Spokes still anchor at the original pivot relative to its translated position; the user sees the menu shifted but spokes still point at the (now off-screen) pivot dot. Pivot dot stays at the original click point so users keep the spatial memory of "I clicked here, this menu is for that node".

## 8. Animation

Pure CSS keyframes, no library:

```css
@keyframes rmenu-spoke-in {
  from { opacity: 0; transform: scaleX(0); }
  to   { opacity: 0.55; transform: scaleX(1); }
}
@keyframes rmenu-pill-in {
  from { opacity: 0; transform: translate(-50%, -50%) scale(0.4); }
  to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
}
```

- Spokes: 200 ms `ease-out`, no stagger (all draw together so the web "snaps" out).
- Pills: 220 ms `cubic-bezier(0.34, 1.56, 0.64, 1)` (slight overshoot), staggered by 30 ms in clockwise order.
- Total open: ~370 ms.
- Close: 150 ms reverse, no stagger. Inline create entering replaces menu without an extra fade.

`prefers-reduced-motion: reduce` → all durations collapse to 0 ms (instant open/close, no scale).

## 9. Keyboard a11y

- **Open from card:** Shift+F10 or `ContextMenu` key on a focused `OrgUnitNode`. Pivot point for menu = card's bounding box center.
- **Inside menu:**
  - `Tab` — cycles items (browser-default focus order; we set `tabIndex={0}` on each pill in order).
  - `ArrowLeft` / `ArrowRight` / `ArrowUp` / `ArrowDown` — move focus to the next item clockwise/counter-clockwise. Wraps.
  - `Enter` / `Space` — activate focused item.
  - `Escape` — close menu, restore focus to the originating card.
- **Inside inline create:** `Enter` submits, `Escape` cancels & restores focus to the originating card.
- **Click-outside:** closes the menu (mouse) but does *not* restore focus (mouse user is in pointer mode).
- **Focus trap:** while menu is open, focus cannot leave the menu via Tab (we use a small focus-trap ring). Implemented manually with `onKeyDown` on the menu container — no new dep.

ARIA:
- Menu container: `role="menu" aria-label="Actions for <unit name>"`
- Each pill: `role="menuitem"` with text content as the accessible name. Delete pill adds `aria-label="Delete <unit name>"`.
- Spokes are `aria-hidden="true"` (purely decorative).
- Pivot dot is `aria-hidden="true"`.

## 10. Mutations

### 10.1 Delete

1. User picks Delete → menu closes, `<DangerConfirmDialog>` opens with title "Delete *North America*?", body "This will also delete all its sub-units. This cannot be undone.", confirm label "Delete unit".
2. Confirm → `useDeleteOrgUnit().mutateAsync(unitId)`. The hook already invalidates the `['org-units']` query.
3. Success: dialog closes, toast `"Unit deleted"`, side panel resets if `selectedId === deletedId`.
4. Failure: dialog stays open, error message rendered inline (toast on hard 5xx).

### 10.2 Create child

1. User picks a child-type item → menu collapses, inline-create pill appears at pivot.
2. User types a name and presses Enter → `useCreateOrgUnit().mutateAsync({ name, unit_type, parent_unit_id })`. Empty name shakes the input and stays focused (no submit).
3. Success: inline-create unmounts, toast `"<type> created"`, the new unit appears in the graph on the next refetch (TanStack Query already invalidates).
4. Failure (e.g. backend rejects `client_account` for non-agency tenants, or duplicate name): inline error message under the input, input stays focused.

## 11. Edge Cases

| Case | Behavior |
|---|---|
| Right-click on the root company unit | No Delete item; child-type items appear normally. |
| Right-click on a team | Only Delete item; no child-type items. |
| User has no permission to delete (`is_accessible === false` or backend RBAC) | Backend returns 403; show toast `"You don't have permission to delete this unit"`. Menu doesn't pre-filter — RBAC is server-truth. |
| Network failure mid-create | Inline error in the form; user can retry with the same input still in the field. |
| Direction toggle pressed while menu is open | Menu closes (it's anchored to a node position that just moved); no action committed. |
| User right-clicks a different node while menu is open for node A | Menu re-anchors to node B; previous menu state discarded. |
| Tenant `workspace_mode !== 'agency'` and user picks Client account | Backend returns 400; surfaced as inline error in the create form. |

## 12. Testing

Vitest + RTL.

### 12.1 `unit-children-rules.test.ts`

- `team` → empty array.
- `client_account` → `[region, division, team]` (no client_account).
- `company` / `region` / `division` → all four child types.

### 12.2 `OrgUnitContextMenu.test.tsx`

- Renders one pill per item passed in props.
- Pills appear at the expected angles (assert via `data-angle` attribute we expose).
- Click a pill → calls the right callback (`onPickDelete` / `onPickChild`).
- Escape key → calls `onClose`.
- ArrowRight cycles focus to the next item; ArrowLeft to previous; both wrap.
- `prefers-reduced-motion: reduce` → no animation classes applied.

### 12.3 `OrgUnitInlineCreate.test.tsx`

- Renders the type chip + input.
- Enter on a non-empty input → calls `onSubmit(name)` with the trimmed name.
- Enter on an empty input → does *not* call `onSubmit`; input stays focused.
- Escape → calls `onCancel`.

### 12.4 `OrgGraph.test.tsx` (extend existing)

- Add: right-click on a node opens the menu (assert `role="menu"` is in the document with the unit's name in `aria-label`).
- Add: pressing Shift+F10 on a focused card opens the menu.
- Add: clicking the Delete pill calls the `onDelete` prop with the unit id.
- Add: clicking a child-type pill renders the inline-create form.

### 12.5 Out of Scope for Tests

- Animation timing (browsers run the keyframes; we test the classes are applied).
- Spoke rendering geometry (covered visually).
- TanStack Query invalidation (already tested in the existing org-units flow tests).

## 13. Verification Before Merging

```bash
cd frontend/app
npm run test
npm run lint
npm run build
```

Manual smoke at `/settings/org-units`:

- [ ] Right-click on the BinQle root → menu opens with no Delete, and Region/Division/Client/Team items.
- [ ] Right-click on a team unit → menu opens with only Delete.
- [ ] Right-click on a non-root unit → menu opens with Delete + valid child types.
- [ ] Spokes draw smoothly; pills pop in with overshoot stagger.
- [ ] Click a child-type pill → inline form appears at the pivot. Type a name, press Enter → new unit appears in the graph after a beat.
- [ ] Click Delete → confirm dialog shows the unit name. Confirm → unit and its descendants disappear from the graph.
- [ ] Tab to a card, press Shift+F10 → menu opens, ArrowRight cycles items, Enter activates.
- [ ] Escape from menu → focus returns to the card.
- [ ] Right-click near the canvas edge → menu shifts inward; pivot dot stays at click point.

## 14. Open Questions

None. All decisions in §3 ratified during the 2026-04-26 brainstorm.

## 15. References

- xyflow v12 `onNodeContextMenu`: https://reactflow.dev/api-reference/types/react-flow-props
- Spec sibling: `2026-04-26-org-graph-xyflow-design.md` (the underlying graph rewrite)
- Backend nesting rules: `backend/nexus/app/modules/org_units/service.py::create_org_unit`
- Existing mutation hooks: `frontend/app/lib/hooks/use-create-org-unit.ts`, `use-delete-org-unit.ts`
- Confirm dialog primitive: `frontend/app/components/px/DangerConfirmDialog.tsx`
- Companion mockup: `.superpowers/brainstorm/<session>/content/radial-menu.html`
