# Daylight Theme — Page-Polish Implementation Plan

> **For agentic workers:** execute task-by-task; each task is a small mechanical recolor sweep with a single combined spec+quality review after.

**Goal:** Sweep the remaining route-level hardcoded colors (old teal/ochre + generic Tailwind palettes) across the recruiter dashboard so every surface matches the **Iris** daylight palette — recoloring the intentional *category* palettes (pipeline stage journey, kanban avatars, job-type bars) to Iris-harmonious sets, and replacing warm leftovers + generic colors with tokens.

**Architecture:** Foundation already swapped the design tokens (`theme.css` `daylight`/Iris block) + primitives. This phase only touches **component/page files** that hardcode colors instead of using tokens. No token or AppShell changes. Verify with `type-check` + `lint` (NOT `next build` — it collides with the live dev `.next`); the user verifies visually.

**Scope guardrails:** `frontend/app` only. Never touch `frontend/session`/`backend`/pre-existing proctoring work. Leave purely-neutral shadows (`rgba(0,0,0,0.x)`, `rgba(15,23,42,…)` slate) as-is unless trivially tokenizable — focus on COLORED hardcodes + old-warm leftovers.

---

## Iris mapping reference (used by every task)

**Tokens (already defined in `theme.css`):** ink `--px-fg` `#0C2A38`; accent (violet) `--px-accent` `#6C5CD0` / `--px-accent-2` `#5848B8` / `--px-accent-soft` `#9B8FE0`; semantic role/`-fill`/`-bg`/`-line` for `ok` (mint-teal), `ai` (cyan), `human` (lavender), `neutral-cat` (mauve), `caution` (amber), `danger` (coral-red); surfaces `--px-bg-2`, `--px-surface-2/3`, hairlines, shadows. Prefer `var(--px-…)` in inline styles where a token fits.

**Category palettes to recolor (use these exact values):**

- **Pipeline stage journey** (`STAGE_TYPE_PALETTE`, `{fill, edge, label}`) — lavender→cyan→mint (cool eval) → amber/gold (warm human decision):
  ```
  intake:          fill #E8E3F7  edge #C9BEF0  label #4A3E7A
  phone_screen:    fill #DEEFF4  edge #BBE0EC  label #1A5E6E
  take_home:       fill #CFEAEF  edge #93D3DE  label #15616D
  ai_screening:    fill #D6F0E8  edge #9FDCC9  label #0B3D34
  human_interview: fill #F6E7C6  edge #E6C277  label #7A4A08
  debrief:         fill #F1DBA8  edge #D7A648  label #5A3810
  ```
- **Kanban avatar set** (6 mid-deep Iris tones, white initials):
  ```
  #6C5CD0  #3F7FB5  #1F8497  #2F8E73  #9A5BA8  #C0607E
  ```
- **Tracker job-type bar set** (6 distinct Iris hues):
  ```
  #6C5CD0  #1F8497  #2C8472  #E8930C  #C0607E  #5C6B73
  ```
- **Connector / link lines** → violet accent: base `var(--px-accent)`, soft `var(--px-accent-soft)`, tint `var(--px-accent-line)`.
- **Old-warm leftovers** → neutral/token: `rgba(58,45,28,a)` → `rgba(20,40,60,a)`; teal `#0E6F63`/`#0A564D`/`rgba(14,111,99,a)` → the appropriate Iris token (`--px-accent`/`--px-ok`/`--px-ai`) or `rgba(20,40,60,a)` for shadows; old beige hexes → `--px-surface*`/`--px-bg*`.
- **Generic Tailwind status colors** (`#10b981` emerald, `#3b82f6` blue, `#f59e0b` amber, `#6b7280`/`#71717a` gray, `#ec4899` pink, `#8b5cf6` violet) → nearest Iris token: emerald→`--px-ok`(+`-bg`), blue→`--px-ai`, amber→`--px-caution`, gray→`--px-fg-3`/`--px-neutral-cat`, pink/violet→`--px-human`/`--px-accent`.

For each task: read the file(s), replace per the mapping above (preserving each palette's *purpose* — distinct-per-category, journey order), keep layout/logic unchanged, run `npm run type-check` + `npx eslint <files>`, commit. Keep AA: any text-on-fill uses the dark `label`/`-role` ink.

---

## Task 1: Pipeline funnel
**Files:** `components/dashboard/pipeline/JobPipelineFunnel.tsx`, `components/dashboard/pipeline/StageConnectorOverlay.tsx`, `components/dashboard/pipeline/StageConfigDrawer.tsx`
- Recolor `STAGE_TYPE_PALETTE` to the Iris stage journey above. Recolor the repeated edge-color usages (lines ~1461-1540) to match the new edges. Replace `rgba(58,45,28,0.16)` → `rgba(20,40,60,0.16)`; `#999` greys → `var(--px-fg-4)`. Connector overlay blue (`#60a5fa`/`#2563eb`/`rgba(37,99,235,…)`) → `var(--px-accent-soft)` / `var(--px-accent)` / `var(--px-accent-line)`. StageConfigDrawer single color → token.
- Commit: `style(app-pipeline): recolor stage palette + connectors to Iris`

## Task 2: Tracker / kanban
**Files:** `components/dashboard/tracker/CandidateKanbanCard.tsx`, `components/dashboard/tracker/TrackerJobCard.tsx`
- `AVATAR_COLORS` → the Iris avatar set. `BAR_COLORS` → the Iris job-type bar set. `statusPillStyle`: "live" → `{ bg: 'var(--px-ok-bg)', fg: 'var(--px-ok)' }`, fallback → `{ bg: 'var(--px-surface-3)', fg: 'var(--px-fg-3)' }`. Leave slate `rgba(15,23,42,…)` shadows and `#d1d5db` (or swap `#d1d5db`→`var(--px-surface-3)`).
- Commit: `style(app-tracker): recolor avatar/bar/status palettes to Iris`

## Task 3: JD + jobs pages
**Files:** `components/dashboard/jd-panels/JobDraftEditor.tsx`, `app/(dashboard)/jobs/[jobId]/questions/page.tsx`, `app/(dashboard)/jobs/page.tsx`, `app/(dashboard)/candidates/CandidateListView.tsx`, `app/(dashboard)/page.tsx`
- Replace hardcoded hex/rgba with the nearest Iris token per the mapping. On `app/(dashboard)/page.tsx`, the dashboard "attention card" `accent` prop values (passed to `AttentionCard`) → Iris semantic colors (`var(--px-ai)` / `var(--px-ok)` / `var(--px-caution)` as fits each card's meaning). 
- Commit: `style(app-jobs): token-sweep JD/jobs/candidates/dashboard colors to Iris`

## Task 4: Org-units
**Files:** `app/(dashboard)/settings/org-units/[unitId]/detail.css`, `app/(dashboard)/settings/org-units/page.tsx`, `components/dashboard/org-units/OrgUnitContextMenu.tsx`, `components/dashboard/org-units/OrgUnitNode.tsx`
- `detail.css`: teal `rgba(14,111,99,…)` gradient + any old hexes → Iris (`var(--px-accent)` family or `rgba(20,40,60,…)`); also fix the stale "warm-light palette" comment noted in earlier review. Sweep the page/menu/node hexes to tokens.
- Commit: `style(app-org-units): recolor detail.css + org canvas to Iris`

## Task 5: Small surfaces + primitives + SignalsPanel a11y
**Files:** `app/(dashboard)/profile/page.tsx`, `components/settings/integrations/JobStatusFilterDialog.tsx`, `components/px/Tabs.tsx`, plus add `aria-hidden="true"` to the `.px-dot` in `components/dashboard/jd-panels/SignalsPanel.tsx` (consistency w/ Badge).
- Sweep remaining hardcoded hex/rgba → tokens. (Profile page already uses tokens mostly; check the 3 flagged.)
- Commit: `style(app-misc): token-sweep profile/integrations/tabs + SignalsPanel dot a11y`

## Final
- Full `npm run lint` (our touched files must be clean; pre-existing repo errors unchanged) + `npm run type-check`.
- Confirm no leftover old-warm/teal in touched files: `grep -rniE '58, ?45, ?28|14, ?111, ?99|0E6F63|FCFAF6|F6F2EC|FFC428' <touched files>` → empty.
- User does the visual pass (clean `.next` restart).
