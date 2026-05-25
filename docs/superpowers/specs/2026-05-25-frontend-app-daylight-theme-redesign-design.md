# Frontend (app) — "Daylight" theme & design-language redesign

**Date:** 2026-05-25
**Surface:** `frontend/app` (recruiter dashboard) only
**Status:** Design — pending implementation plan

---

## 1. Goal

Replace the current warm beige/teal **`warm-light`** look — which the user finds "old and boring" — with a **light, glassy, modern** design language that feels *fun* and *AI-first* while staying enterprise-credible and legible for all-day data work.

This is a design change, not a plumbing change: the token architecture from the 2026-05-25 brand-theme centralization already makes a theme swap a one-block edit in `app/theme.css` plus a font swap in `app/layout.tsx`, and Tailwind named-palette utilities resolve through per-theme `--c-*` vars so the whole app recolors. The new work beyond tokens is: the **glass AppShell frame**, the **Urbanist** font swap, a **retune of every `components/px/*` primitive + `globals.css` `.px-*` utilities**, and a **page-by-page polish pass**.

### Out of scope
- `frontend/session` (candidate surface) and `frontend/admin` — untouched. Separate brand surfaces, later phases.
- All backend.
- **Dark mode** — explicitly deferred (CLAUDE.md says dark is out of MVP scope). The architecture supports adding a dark `[data-px-theme]` block later; we design tokens so that's a clean future add, but we ship **one** new light theme now.
- Renaming the product or touching `lib/brand.ts` name/logo fields (name "BinQle.ai" stays provisional as-is).

---

## 2. Design language — principles

1. **Light base, crisp structure.** Near-white surfaces, near-black ink (softened from pure `#000` to kill glare), generous whitespace.
2. **Fluid glass chrome.** Sidebar + topbar are **one continuous frosted-glass frame** (no internal divider) over a whisper-pastel gradient backdrop. The main content panel floats *inside* the frame as a solid white rounded card with an even gutter on all sides.
3. **The "tasteful glass" rule (load-bearing).** Glass (`backdrop-filter`) is used **only on chrome and ephemeral surfaces**: the shell frame, dialogs, popovers/dropdowns, drawers, the AI Copilot panel, toasts. **Data surfaces stay solid:** the content pane, tables, dense lists, form fields, and data-bearing cards are opaque white. This is what keeps glass tasteful and keeps dense data legible. Validated against ui-ux-pro-max: glassmorphism best-for = "modern SaaS / financial dashboards / nav + modal overlays."
4. **Pastels as a category system.** The four pastels *encode meaning* (status / signal type / stage), never decorate randomly. Color is **never the sole signal** — every status pairs its pastel with a text label and a dot/icon.
5. **Yellow is the signature, used sparingly.** `#FFC428` marks interaction and brand moments (active nav, primary buttons, key highlights) — not large fills.

---

## 3. Color system

All values live in a new `[data-px-theme="daylight"]` block in `app/theme.css`, **replacing** the `warm-light` block. `ThemeName` in `lib/brand.ts` changes `"warm-light"` → `"daylight"`; `brand.theme` is set to `"daylight"`.

### 3.1 Source palette (user-chosen)
`#000000` · `#FFC428` · `#DCACAF` · `#A1D8C1` · `#ACD7DC` · `#DCD7AC` · `#FFFFFF`, plus two **functional colors added** (the source palette has no danger color, and reusing brand-yellow for warnings would conflict with its interactive role):
- **Caution / attention:** `#E8930C` (amber — deliberately more orange than the brand yellow so "warning" reads distinct from "clickable").
- **Danger / destructive:** `#E5484D` (red — same family as the standard `#EF4444`/`#DC2626`).

### 3.2 Structural tokens

| Token | Value | Role |
|---|---|---|
| `--px-fg` | `#0E0E0E` | primary ink (softened black) |
| `--px-fg-2` | `#3A3A38` | secondary ink |
| `--px-fg-3` | `#65625C` | tertiary / muted |
| `--px-fg-4` | `#9A968E` | placeholder / faint |
| `--px-fg-5` | `#C9C6BE` | disabled ink |
| `--px-bg` | `#F2F1EC` | app backdrop base (under the glass) |
| `--px-bg-2` | `#EAE9E3` | recessed |
| `--px-surface` | `#FFFFFF` | solid content surface |
| `--px-surface-2` | `#F6F5F1` | subtle raised tint |
| `--px-surface-3` | `#ECEAE3` | borders/fills step |
| `--px-hairline` | `rgba(14,14,14,0.08)` | hairline |
| `--px-hairline-strong` | `rgba(14,14,14,0.14)` | input borders |
| `--px-divider` | `rgba(14,14,14,0.05)` | dividers |

The **glass backdrop** (set on `<body>`, fixed) is a faint multi-stop gradient: powder-blue top-left + sand bottom-right + a whisper of rose, all ≤ 0.34 alpha, over a `linear-gradient(135deg,#EFF2EE,#E7EEF1)`. It is intentionally subtle — the "vibrant background" glass wants, kept enterprise-quiet.

### 3.3 Glass tokens (new)

| Token | Value | Notes |
|---|---|---|
| `--px-glass-blur` | `16px` | within the 10–20px best-practice band |
| `--px-glass-bg` | `rgba(255,255,255,0.55)` | translucent chrome fill |
| `--px-glass-bg-2` | `rgba(255,255,255,0.42)` | topbar (slightly lighter so the bend reads as one piece, not two fills) |
| `--px-glass-border` | `rgba(255,255,255,0.65)` | 1px light edge |
| `--px-glass-sheen` | `linear-gradient(90deg,transparent,rgba(255,255,255,0.85),transparent)` | 1px top highlight on the frame |

**Fallback:** wrap glass surfaces in `@supports (backdrop-filter: blur(1px))`. When unsupported, or under `@media (prefers-reduced-transparency: reduce)`, glass surfaces fall back to a **solid** `--px-surface-2` fill with `--px-hairline-strong` borders. Contrast must hold in both modes.

### 3.4 Accent & semantic tokens

| Token | Value | Role |
|---|---|---|
| `--px-accent` | `#FFC428` | interactive / brand signature |
| `--px-accent-2` | `#E0A800` | accent hover/pressed (darker gold) |
| `--px-accent-ink` | `#1A1505` | text/icon **on** yellow (≈12:1 — passes) |
| `--px-accent-tint` | `rgba(255,196,40,0.16)` | active-nav fill, soft highlights |
| `--px-accent-line` | `rgba(255,196,40,0.42)` | accent borders |
| `--px-ok` | `#A1D8C1` (tint) / `#0C3D2A` (ink) | success / "Strong" / passed |
| `--px-ai` | `#ACD7DC` (tint) / `#1C5563` (ink) | AI-generated, AI Copilot, info |
| `--px-human` | `#DCACAF` (tint) / `#5A2A2D` (ink) | **Borderline**, human-in-loop |
| `--px-neutral-cat` | `#DCD7AC` (tint) / `#5A521F` (ink) | drafts / neutral category |
| `--px-caution` | `#E8930C` | warnings (tint `rgba(232,147,12,0.12)`, ink `#7A4A08`) |
| `--px-danger` | `#E5484D` | destructive/error (tint `rgba(229,72,77,0.10)`, ink `#8A211F`) |

**Token structure per semantic role (important — avoids a contrast bug).** The existing scheme uses `--px-<role>` as the **foreground/text** color (e.g. `.px-badge.ok { color: var(--px-ok) }`), with `--px-<role>-bg` and `--px-<role>-line` for fill and border. Because our pastels are too light to be text, each role gets a **triad** where the foreground is the *dark ink* shade and a new `-fill` holds the pastel:

| Role | `--px-<role>` (text/fg = dark ink) | `--px-<role>-fill` (solid pastel) | `--px-<role>-bg` (tint) | `--px-<role>-line` (border) |
|---|---|---|---|---|
| ok | `#0C3D2A` | `#A1D8C1` | `rgba(161,216,193,0.18)` | `rgba(161,216,193,0.45)` |
| ai | `#1C5563` | `#ACD7DC` | `rgba(172,215,220,0.18)` | `rgba(172,215,220,0.45)` |
| human | `#5A2A2D` | `#DCACAF` | `rgba(220,172,175,0.18)` | `rgba(220,172,175,0.45)` |
| neutral-cat | `#5A521F` | `#DCD7AC` | `rgba(220,215,172,0.20)` | `rgba(220,215,172,0.5)` |
| caution | `#7A4A08` | `#E8930C` | `rgba(232,147,12,0.12)` | `rgba(232,147,12,0.4)` |
| danger | `#8A211F` | `#E5484D` | `rgba(229,72,77,0.10)` | `rgba(229,72,77,0.35)` |

**Rule:** pastels live in `-fill`/`-bg`/`-line` only. Any text (incl. badge/pill text on a tint) uses `--px-<role>` (the dark ink), verified ≥4.5:1. Pastels never carry body text on white. The full-pastel `-fill` is for solid blocks (kanban column header, stat-card bg) where text sits on it as dark ink.

### 3.5 `--c-*` named-palette ramps
The `[data-px-theme]` block also redefines the Tailwind named-palette ramps (`--c-zinc-*`, `--c-red-*`, `--c-green-*`, `--c-blue-*`, `--c-amber-*`, `--c-purple-*`, etc.) the same way `warm-light` did, so existing `bg-zinc-100` / `text-red-600` / `text-emerald-700` call-sites across the app recolor coherently:
- neutrals (`zinc/neutral/stone/slate/gray`) → cool-neutral ramp anchored on the new ink/surface values.
- `red/rose` → danger `#E5484D` ramp. `green/emerald/teal` → mint-derived ramp (tints light, 600/700 = readable dark green ink). `blue/sky/indigo` → powder-blue-derived ramp. `amber/yellow/orange` → split: 50–100 = yellow tints, 500 = `#E8930C` caution, 700+ = dark amber ink. `purple/violet/fuchsia` → rose/human-derived ramp.

(Exact 50→950 stops are produced during implementation; the constraint is: 50/100 = tinted bg, 500/600 = saturated, 700/900 = legible text, all AA against white.)

### 3.6 shadcn-compat tokens
The shadcn semantic aliases at the bottom of the `warm-light` block (`--background`, `--primary`, `--ring`, `--sidebar*`, `--chart-*`, etc.) are re-pointed to the new `--px-*` values. Notably `--ring` → see focus-ring note (§5), `--chart-*` → accent / ai / caution / human / ok for data viz, `--primary-foreground` → `--px-accent-ink` (black on yellow), `--destructive` → `--px-danger`.

---

## 4. Typography

`app/layout.tsx`: replace `Inter`/`Fraunces` with **Urbanist** (weights 400/500/600/700/800; italics not needed). **JetBrains Mono retained** for IDs/code/tabular numbers.

- `--font-sans` → Urbanist. `--font-serif` **and** `--font-heading` → Urbanist (so existing `.px-serif` / `var(--font-serif)` references — e.g. `.px-dialog-title` — keep working and render Urbanist). `--font-mono` → JetBrains Mono.
- Heading weights retune: today's serif titles are weight 400; Urbanist headings want **700–800** with tight tracking (`-0.3px to -0.5px`). Update `.px-dialog-title` and heading usages accordingly.
- `next/font` self-hosts + applies `font-display: swap` and a size-adjusted fallback automatically → no FOIT, no font-swap layout shift.
- **Tabular figures:** numeric data columns/metrics use `font-variant-numeric: tabular-nums` (Urbanist) or JetBrains Mono, to prevent row jitter. Add a `.px-num` utility.
- Body stays ≥ 13.5px (dense dashboard); never below 12px for primary text.

---

## 5. The glass shell — `components/dashboard/AppShell.tsx`

Restructure to the approved fluid frame:
- `<body>` carries the fixed pastel-gradient backdrop (§3.2).
- Sidebar + topbar render as **one continuous glass surface** — both use `--px-glass-*`, **no `border-right` / `border-bottom`** between them; a single 1px `--px-glass-sheen` highlight runs across the top of the whole frame. The corner where sidebar meets topbar is seamless.
- Main content = solid `--px-surface` (white) panel, `--px-r-xl` radius, `--px-shadow-md/lg`, with an even margin gutter on all four sides → the "wrap" effect.
- **Active nav item:** soft `--px-accent-tint` fill + near-black text + a 3px `--px-accent` left indicator bar (not a full yellow fill — keeps the persistent nav calm). Hover = `rgba(255,255,255,0.45)`.
- `--px-topbar-h` stays the chrome-height contract other layouts depend on (e.g. master-detail asides) — keep the variable, just restyle.
- Respect `prefers-reduced-motion` and the glass fallback (§3.3).

### Focus indicator (best-practice fix)
A plain yellow ring on white is ~1.4:1 — **fails** the ≥3:1 focus-indicator requirement. So:
- **Keyboard focus (`:focus-visible`)** = `2px solid var(--px-fg)` ring + `2px` offset (high-contrast, on-brand ink), OR equivalently a `0 0 0 2px var(--px-surface), 0 0 0 4px var(--px-fg)` box-shadow halo on busy backgrounds.
- `--ring` (shadcn) and all `.px-*:focus-visible` rules adopt this. Yellow is reserved for the **active/selected** state, not the focus ring.
- The current `.px-btn:focus-visible { box-shadow: 0 0 0 3px var(--px-accent-tint) }` and `.px-input:focus { box-shadow: 0 0 0 3px var(--px-accent-tint) }` get an additional high-contrast outline so keyboard focus is unmistakable; mouse focus can keep the soft tint.

---

## 6. Primitive retune — `components/px/*` + `globals.css` `.px-*`

Retune (not restructure) every primitive to the new tokens. Sweep **all hardcoded warm hex** out of `globals.css` to tokens: `#1E1B16` / `#F6F2EC` (tooltip), `rgba(30,27,22,…)` (dialog backdrop), `rgba(58,45,28,…)` (shadows), teal `rgba(14,111,99,…)` (stage-glow / shimmer / progress) → accent/neutral tokens.

| Primitive | Change |
|---|---|
| `Button` / `.px-btn` | primary = yellow bg + `--px-accent-ink` text; secondary = surface-2; outline = hairline; **destructive = `--px-danger`**; ghost/link unchanged in structure. Add **loading state** (spinner + `disabled`) so async buttons can't double-submit. |
| `Badge` / `.px-badge`, `.px-chip` | map variants to the semantic set (ok/ai/human/neutral/caution/danger/accent); each variant = pastel tint bg + same-hue dark ink + **a `.px-dot`** so meaning isn't color-only. |
| `Card` | solid white default (`--px-surface`, `--px-shadow-sm/md`, hairline). Add an explicit **`glass` variant** for overlay/ephemeral contexts only. |
| `Input`/`Textarea`/`Select`/checkbox/radio/switch (`.px-input` family) | recolor to tokens; **focus = high-contrast ring (§5)**; selected/checked = `--px-accent` with `--px-accent-ink` checkmark. |
| `Dialog` / `.px-dialog-*` | backdrop scrim `rgba(14,14,14,0.45)` + blur (40–60% legibility); **content surface = glass** (`--px-glass-*`) per "ephemeral" rule; title → Urbanist 700. |
| `Tooltip` / `.px-tooltip-content` | ink bg → `--px-fg` token; text `--px-surface`. |
| `Skeleton` / shimmer / `stage-generating-*` | recolor teal→accent; keep `prefers-reduced-motion` guards. |
| `Toaster` (sonner) | adopt glass surface + semantic colors; `aria-live` preserved; auto-dismiss 3–5s. |
| Eyebrow / kbd / copilot-strip | recolor to tokens. |

**Cross-app duplication guard (CLAUDE.md):** `components/px/Button.tsx`, `Input.tsx`, `Toaster.tsx`, and the shadcn→px token mapping are listed as "kept in sync with `frontend/session`." This redesign **deliberately diverges** the recruiter app from session (session is a separate brand surface, not rebranded yet). The implementing PR description must call out this divergence explicitly per the CLAUDE.md rule, rather than mirroring the changes into `frontend/session`.

---

## 7. Page-polish pass ("Foundation + page polish" scope)

After Foundation lands (tokens + font + shell + primitives recolor the whole app automatically), do a per-route pass applying glass-on-chrome / pastel-categories / solid-data where generic tokens aren't enough:

- **Dashboard home** (`app/(dashboard)/page.tsx`) — greeting, action cards (pastel category tints), "active roles" + "Today" blocks, activity feed, Copilot brief card.
- **Jobs** — list + 3-panel JD review (`jd-panels/*`): signal cards use the category system; provenance/source chips recolored.
- **Candidates** — kanban (`tracker/*`) + list + detail: status badges (Strong/Borderline/etc.) use color+label+dot; **Borderline stays unmistakably distinct** (product invariant); tables get `overflow-x-auto`, `aria-sort` sortable headers, multi-select + bulk-action bar, tabular numerics.
- **Pipeline editor** (`pipeline/*`) — stage cards, drawers (glass), generating animations recolored.
- **Question bank** — cards, refine panel, status badges.
- **Reports** (placeholder) — set up chart token palette (`--chart-*`) for when it's built; chart colors accessible + not color-only (legends/labels).
- **Settings/Team, Org-units** — tables, the SVG org canvas (`OrgGraphCanvas`/edges) recolored to tokens.
- **Auth / onboarding / invite / suspended** — centered glass card on the gradient backdrop; migrate any raw-hex.

Each page-polish change keeps composition-test conventions (parent+child, mock at API boundary).

---

## 8. Files to change (primary)

- `app/theme.css` — replace `warm-light` block with `daylight` (structural + glass + semantic + `--c-*` ramps + shadcn aliases).
- `lib/brand.ts` — `ThemeName` `"warm-light"`→`"daylight"`; `brand.theme = "daylight"`.
- `app/layout.tsx` — Urbanist + JetBrains Mono (drop Inter/Fraunces); `data-px-theme="daylight"`.
- `app/globals.css` — font `@theme` vars; sweep `.px-*` hardcoded hex → tokens; add `.px-num`, glass utility/variant, focus-ring rules, `@supports`/`prefers-reduced-transparency` fallbacks.
- `components/dashboard/AppShell.tsx` — fluid glass frame + active-nav + floating content pane.
- `components/px/*` — Button/Badge/Card/Input/Select/Dialog/Tooltip/Toaster/Skeleton retune (+ Button loading state, Card glass variant, Badge dot).
- Page components under `app/(dashboard)/**` and `components/dashboard/**` — polish pass (§7).
- Tests under `tests/**` — update any snapshot/class assertions touched; add coverage for new Button loading + Badge dot states.

---

## 9. Accessibility & best-practice guardrails (gate)

Derived from the ui-ux-pro-max pass; all must hold before "done":
- **Contrast:** every text/bg pair ≥ 4.5:1 (AA). Pill/category text uses its hue's dark-ink shade. Black-on-yellow button text ≈ 12:1 ✓.
- **Focus visible & high-contrast:** §5 ink ring, ≥3:1 vs adjacent; never `outline:none` without replacement.
- **Color never alone:** status/category = color + label + dot/icon.
- **Glass legibility:** text over glass meets 4.5:1; modal scrim 40–60%; solid fallback when transparency unsupported/reduced.
- **Motion:** micro-interactions 150–300ms, `ease-out` enter / `ease-in` (faster) exit; no decorative infinite animation; `prefers-reduced-motion` honored (already in `globals.css`).
- **Loading/feedback:** skeletons for >300ms; async buttons disable + spinner.
- **Tables:** sortable `aria-sort`, horizontal-scroll wrapper, tabular figures, bulk actions.
- **Keyboard:** existing dialog/drawer focus-move and dnd `KeyboardSensor` patterns preserved.
- **Icons:** SVG only (existing convention; no emoji).
- **No dark-mode-by-default** (we're light-default — matches the anti-pattern guidance).

---

## 10. Verification

- `npm run build`, `npm run lint`, `npm run type-check`, `npm run test` — all green (CLAUDE.md gate).
- **Verify the *served* frontend, not just tests/build** (per prior feedback — Next dev serves stale CSS after git churn): clean `.next`, restart dev server, `curl -I` / curl the HTML+CSS for representative routes and confirm Urbanist + token values are actually applied; spot-check in-browser at 1280px and 1440px (`3xl`).
- Manual contrast check on the semantic pills + glass nav text.
- Confirm `grep livekit frontend/app/package.json` still clean (no accidental cross-surface import) and that `frontend/session` was intentionally left unchanged.

---

## 11. Build sequence (for the plan)

1. **Tokens + font** — `theme.css` `daylight` block, `brand.ts`, `layout.tsx`, `globals.css` `@theme` + hex sweep + focus-ring + glass utilities/fallbacks. (App recolors app-wide; verify served CSS.)
2. **Shell** — `AppShell.tsx` fluid glass frame + active-nav + floating pane.
3. **Primitives** — `components/px/*` retune + new states; update affected tests.
4. **Page polish** — per-route pass (§7), route by route, each with its tests.
5. **A11y/verification gate** — §9 + §10 before calling done.

Steps 1–3 are "Foundation"; step 4 is "page polish." Step 1 alone produces a coherent (if un-polished) new look everywhere; later steps refine.
