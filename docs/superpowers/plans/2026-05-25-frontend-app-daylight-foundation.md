# Daylight Theme — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `frontend/app`'s warm beige `warm-light` look with a new light, glassy "daylight" design language — Urbanist font, the user's pastel palette as a semantic category system, a fluid frosted-glass shell, and AA-accessible focus/contrast — such that the whole app recolors coherently.

**Architecture:** The token architecture already in place means a theme swap is a one-block edit in `app/theme.css` (Tailwind named-palette utilities resolve through per-theme `--c-*` vars) plus a font swap in `app/layout.tsx`. Foundation = tokens + font + a `globals.css` sweep/new-utilities + the `AppShell` glass treatment + retuning the `components/px/*` primitives. A later, separate plan does the page-by-page polish.

**Tech Stack:** Next.js 16, Tailwind CSS v4 (`@theme` in `globals.css`, no config file), `@base-ui-components/react`-based in-house `px/` primitives, `next/font/google`, Vitest + Testing Library.

**TDD adaptation (read this):** Pure visual token/CSS changes cannot be meaningfully unit-tested by hex value, and this is a solo-dev repo validated by *serving the frontend* (see "Verify served frontend" convention). So: **CSS/token tasks** are verified by `type-check` + `build` + serving the dev server and curling the HTML/CSS. **Component-logic tasks** (Button `loading`, Badge `dot`/`human`) use real TDD (failing Vitest test first). Each task states which mode it uses.

**Spec:** `docs/superpowers/specs/2026-05-25-frontend-app-daylight-theme-redesign-design.md`

**Scope guardrails:**
- Touch **only** `frontend/app`. Do not modify `frontend/session`, `frontend/admin`, or backend.
- `components/px/Button.tsx`, `Input.tsx`, `Toaster.tsx`, and the token mapping are flagged in CLAUDE.md as "kept in sync with `frontend/session`." This redesign **deliberately diverges** them. Do NOT mirror changes into `frontend/session`; the divergence is intentional (separate brand surface).
- Dark mode is out of scope (one new light theme only).

**Working directory for all commands:** `frontend/app/`

---

## Reference values (used across tasks)

These are the exact token values the `daylight` block uses. Tasks below reference this table.

**Structural**
```
--px-fg:#0E0E0E  --px-fg-2:#3A3A38  --px-fg-3:#65625C  --px-fg-4:#9A968E  --px-fg-5:#C9C6BE
--px-bg:#F2F1EC  --px-bg-2:#EAE9E3
--px-surface:#FFFFFF  --px-surface-2:#F6F5F1  --px-surface-3:#ECEAE3
--px-hairline:rgba(14,14,14,0.08)  --px-hairline-strong:rgba(14,14,14,0.14)  --px-divider:rgba(14,14,14,0.05)
```

**Glass**
```
--px-glass-blur:16px
--px-glass-bg:rgba(255,255,255,0.55)   --px-glass-bg-2:rgba(255,255,255,0.42)
--px-glass-border:rgba(255,255,255,0.65)
--px-glass-sheen:linear-gradient(90deg,transparent,rgba(255,255,255,0.85),transparent)
```

**Backdrop gradient** (set on `<body>`, fixed)
```
radial-gradient(72% 58% at 6% 2%, rgba(172,215,220,0.34), transparent 60%),
radial-gradient(64% 72% at 102% 104%, rgba(220,215,172,0.34), transparent 60%),
radial-gradient(50% 50% at 60% -10%, rgba(220,172,175,0.16), transparent 70%),
linear-gradient(135deg,#EFF2EE,#E7EEF1)
```

**Accent**
```
--px-accent:#FFC428  --px-accent-2:#E0A800  --px-accent-ink:#1A1505
--px-accent-tint:rgba(255,196,40,0.16)  --px-accent-line:rgba(255,196,40,0.42)
--px-accent-soft:#FFD874   --px-accent-glow:rgba(255,196,40,0.28)
```

**Semantic triads** (fg = dark ink for text; -fill = solid pastel; -bg = tint; -line = border)
```
ok:      --px-ok:#0C3D2A  --px-ok-fill:#A1D8C1  --px-ok-bg:rgba(161,216,193,0.18)  --px-ok-line:rgba(161,216,193,0.45)
ai:      --px-ai:#1C5563  --px-ai-fill:#ACD7DC  --px-ai-bg:rgba(172,215,220,0.18)  --px-ai-line:rgba(172,215,220,0.45)
human:   --px-human:#5A2A2D  --px-human-fill:#DCACAF  --px-human-bg:rgba(220,172,175,0.18)  --px-human-line:rgba(220,172,175,0.45)
neutralc:--px-neutral-cat:#5A521F  --px-neutral-cat-fill:#DCD7AC  --px-neutral-cat-bg:rgba(220,215,172,0.20)  --px-neutral-cat-line:rgba(220,215,172,0.5)
caution: --px-caution:#7A4A08  --px-caution-fill:#E8930C  --px-caution-bg:rgba(232,147,12,0.12)  --px-caution-line:rgba(232,147,12,0.4)
danger:  --px-danger:#8A211F  --px-danger-fill:#E5484D  --px-danger-bg:rgba(229,72,77,0.10)  --px-danger-line:rgba(229,72,77,0.35)
```
> Note: `--px-ok`/`--px-ai`/etc. become **dark ink** (was a saturated mid-tone in warm-light). Any existing CSS using `color: var(--px-ok)` on a tint still reads correctly because the ink is dark. Solid-fill contexts use the new `-fill` vars.

**Shadows / radii / motion** (recolor warm→neutral; keep the same scale + variable names)
```
--px-shadow-sm:0 1px 2px rgba(14,14,14,0.05)
--px-shadow-md:0 8px 24px rgba(40,45,60,0.08), 0 2px 4px rgba(40,45,60,0.04)
--px-shadow-lg:0 24px 60px rgba(40,45,60,0.14), 0 6px 16px rgba(40,45,60,0.06)
--px-ease, --px-d1/2/3, --px-r-* : unchanged
--px-row-h / py / group-gap / topbar-h : unchanged
```

**`--c-*` named-palette ramps** — keep the warm-light *structure* (every family redefined), recolor anchors. Interpolate intermediate stops smoothly, keeping the warm-light invariant: **50/100 = tint, 200–400 = mid, 500/600 = saturated, 700–950 = legible dark ink (AA on white)**. Anchors per family:
```
white:                       #FFFFFF
zinc/neutral/stone/slate/gray (cool-neutral): 50 #F6F5F1 · 300 #C9C6BE · 500 #65625C · 700 #2A2926 · 900 #141413 · 950 #0B0B0A
red/rose (danger):           50 #FCEBEA · 500 #E5484D · 700 #A82C2C · 900 #6E1C1C
green/emerald/teal (mint):   50 #EAF5F0 · 400 #6FC2A4 · 500 #2C7A5B · 700 #14523A · 900 #0C3D2A
blue/sky/indigo (powder):    50 #EAF3F5 · 500 #2C6B7A · 700 #1C5563 · 900 #123942
amber/yellow/orange (split): 50 #FBF6E4 · 100 #F6ECC4 · 500 #E8930C (caution) · 700 #7A4A08 · 900 #4D2F05
purple/violet/fuchsia (rose):50 #F7ECEC · 500 #B4666A · 700 #5A2A2D · 900 #3E1C1F
```

**shadcn aliases** (bottom of block) — repoint:
```
--background:var(--px-bg)  --foreground:var(--px-fg)
--card:var(--px-surface)  --card-foreground:var(--px-fg)
--popover:var(--px-surface)  --popover-foreground:var(--px-fg)
--primary:var(--px-accent)  --primary-foreground:var(--px-accent-ink)   <-- black on yellow
--secondary:var(--px-surface-2)  --secondary-foreground:var(--px-fg-2)
--muted:var(--px-bg-2)  --muted-foreground:var(--px-fg-3)
--accent:var(--px-surface-2)  --accent-foreground:var(--px-fg)
--destructive:var(--px-danger-fill)
--border:var(--px-surface-3)  --input:var(--px-hairline-strong)
--ring:var(--px-fg)            <-- high-contrast focus ring (NOT yellow)
--chart-1:var(--px-accent)  --chart-2:var(--px-ai-fill)  --chart-3:var(--px-caution-fill)  --chart-4:var(--px-human-fill)  --chart-5:var(--px-ok-fill)
--sidebar:var(--px-bg-2)  --sidebar-foreground:var(--px-fg)
--sidebar-primary:var(--px-accent)  --sidebar-primary-foreground:var(--px-accent-ink)
--sidebar-accent:var(--px-surface-2)  --sidebar-accent-foreground:var(--px-fg)
--sidebar-border:var(--px-surface-3)  --sidebar-ring:var(--px-fg)
--radius:0.5rem
```

---

## Task 1: Theme tokens — the `daylight` block + brand wiring

**Files:**
- Modify: `app/theme.css` (replace the `:root, [data-px-theme="warm-light"]` block)
- Modify: `lib/brand.ts:5` (ThemeName) and `lib/brand.ts:41` (`brand.theme`)

**Mode:** CSS/token (verify by type-check + build + served CSS).

- [ ] **Step 1: Rewrite the theme block in `app/theme.css`**

Replace the selector `:root,\n[data-px-theme="warm-light"]` with `:root,\n[data-px-theme="daylight"]`. Replace every token value inside with the **Reference values** table above: structural, glass (new block), accent, semantic triads (add `-fill` vars; switch `--px-*` role vars to the dark-ink values), shadows, the `--c-*` ramps (anchors above, interpolate stops), and the shadcn aliases. Keep the density modifier block at the bottom unchanged. Update the header comment from "ProjectX v4 — warm-light" to "daylight — light glass". Keep ALL existing variable names that other files depend on (`--px-topbar-h`, `--px-r-*`, `--px-ease`, `--px-d*`, `--px-row-*`, `--px-accent-glow`, `--px-accent-soft`).

- [ ] **Step 2: Update `lib/brand.ts`**

```ts
// line 5
export type ThemeName = "daylight"; // add new names here as themes are added to app/theme.css
// line 41 (inside the brand object)
  theme: "daylight",
```

- [ ] **Step 3: Type-check**

Run: `npm run type-check`
Expected: PASS (0 errors). The `ThemeName` union now only contains `"daylight"`; `brand.theme` matches.

- [ ] **Step 4: Build**

Run: `npm run build`
Expected: build completes; no CSS parse errors from `theme.css`.

- [ ] **Step 5: Verify the served CSS (clean restart)**

```bash
rm -rf .next && npm run dev &   # then wait ~4s for "Ready"
curl -s http://localhost:3000/login | grep -o 'data-px-theme="[^"]*"'
```
Expected: `data-px-theme="daylight"`. Kill the dev server after checking (`kill %1`).

- [ ] **Step 6: Commit**

```bash
git add app/theme.css lib/brand.ts
git commit -m "feat(app-theme): replace warm-light with daylight token block

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Fonts — Urbanist + JetBrains Mono

**Files:**
- Modify: `app/layout.tsx:1-23,38`
- Modify: `app/globals.css:5-9` (comment + heading mapping)

**Mode:** CSS/token (verify by build + served font).

- [ ] **Step 1: Swap the font imports in `app/layout.tsx`**

Replace lines 1-23 imports/instances with:

```tsx
import type { Metadata } from "next";
import { Urbanist, JetBrains_Mono } from "next/font/google";
import { brand } from "@/lib/brand";
import "./globals.css";

const urbanist = Urbanist({
  variable: "--font-sans",
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});
```

Then update the `<html>` className (line ~38) to drop the removed font vars and add a `--font-serif` alias to Urbanist so existing serif references render Urbanist:

```tsx
    <html
      lang="en"
      className={`${urbanist.variable} ${jetbrainsMono.variable} h-full antialiased`}
      style={{ ["--font-serif" as string]: "var(--font-sans)" }}
      data-px-theme={brand.theme}
      data-px-density={brand.density}
    >
```

- [ ] **Step 2: Update the font comment + heading var in `app/globals.css`**

At `app/globals.css` lines 5-9, change the comment to `/* Fonts — Urbanist (sans+headings) + JetBrains Mono */` and keep `--font-heading: var(--font-serif);` (now resolves to Urbanist). Leave `--font-sans`/`--font-mono` mappings as-is.

- [ ] **Step 3: Build**

Run: `npm run build`
Expected: PASS; next/font fetches Urbanist + JetBrains Mono at build (no Inter/Fraunces references remain — grep to confirm: `grep -rn "Fraunces\|Inter(" app/` returns nothing).

- [ ] **Step 4: Verify served font**

```bash
rm -rf .next && npm run dev &   # wait for Ready
curl -s http://localhost:3000/login | grep -oi 'urbanist' | head -1
```
Expected: a match (Urbanist `@font-face`/preload present). Kill dev server after.

- [ ] **Step 5: Commit**

```bash
git add app/layout.tsx app/globals.css
git commit -m "feat(app-theme): switch to Urbanist + JetBrains Mono

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `globals.css` — hex sweep, glass utility, focus ring, fallbacks, `.px-num`

**Files:**
- Modify: `app/globals.css` (`.px-*` blocks + `@layer base` + add utilities)

**Mode:** CSS/token (verify by build + served CSS + manual contrast check).

- [ ] **Step 1: Sweep hardcoded warm hex → tokens**

In `app/globals.css`, replace these literals:
- `.px-tooltip-content`: `background:#1E1B16` → `background:var(--px-fg)`; `color:#F6F2EC` → `color:var(--px-surface)`.
- `.px-dialog-backdrop`: `background: rgba(30, 27, 22, 0.34)` → `rgba(14,14,14,0.45)` (stronger scrim, 45%).
- `stage-glow` / `stage-shimmer` / `stage-progress` keyframes + `.stage-generating-*`: replace teal `rgba(14, 111, 99, …)` with `var(--px-accent)` / `rgba(255,196,40,…)` equivalents (glow `rgba(255,196,40,0.28)`, shimmer `rgba(255,196,40,0.14)`, progress uses `var(--px-accent)`).
- Any remaining `rgba(58, 45, 28, …)` shadow literals → neutral `rgba(40,45,60,…)` (these mostly live in theme.css now; grep `globals.css` for `58, 45, 28` and `30, 27, 22` and replace any stragglers).

Confirm none remain: `grep -nE '14, ?111, ?99|58, ?45, ?28|30, ?27, ?22|1E1B16|F6F2EC' app/globals.css` → empty.

- [ ] **Step 2: Make the dialog title use Urbanist bold**

`.px-dialog-title`: change `font-family: var(--font-serif)` stays (now Urbanist) but `font-weight: 400` → `font-weight: 700`; keep `letter-spacing:-0.4px`.

- [ ] **Step 3: Add high-contrast focus ring (replaces yellow-only focus)**

Append to the buttons section / inputs: update `.px-btn:focus-visible` and `.px-input:focus` and checkbox/radio/switch `:focus-visible` so keyboard focus is a high-contrast ink ring:

```css
/* High-contrast keyboard focus — yellow fails 3:1 on white, so use ink. */
.px-btn:focus-visible,
.px-input:focus-visible,
select.px-input:focus-visible,
.px-check:focus-visible,
.px-radio:focus-visible,
.px-switch:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--px-surface), 0 0 0 4px var(--px-fg);
}
/* Pointer focus on inputs keeps the soft accent affordance. */
.px-input:focus:not(:focus-visible) {
  border-color: var(--px-accent);
  box-shadow: 0 0 0 3px var(--px-accent-tint);
}
```
Remove the old `.px-btn:focus-visible { box-shadow: 0 0 0 3px var(--px-accent-tint); }` and the accent-only `.px-input:focus` box-shadow so they don't conflict (keep `.px-input:focus` border-color via the `:not(:focus-visible)` rule above).

- [ ] **Step 4: Add `.px-glass` utility + reduced-transparency/no-support fallback**

```css
/* ─── Glass (chrome + ephemeral surfaces ONLY) ─── */
.px-glass {
  background: var(--px-glass-bg);
  border: 1px solid var(--px-glass-border);
  -webkit-backdrop-filter: blur(var(--px-glass-blur));
  backdrop-filter: blur(var(--px-glass-blur));
}
@supports not (backdrop-filter: blur(1px)) {
  .px-glass { background: var(--px-surface-2); }
}
@media (prefers-reduced-transparency: reduce) {
  .px-glass {
    background: var(--px-surface-2);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
```

- [ ] **Step 5: Add `.px-num` tabular-figures utility**

```css
.px-num { font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }
```

- [ ] **Step 6: Build + serve + check**

Run: `npm run build` (PASS), then `rm -rf .next && npm run dev &`, then:
```bash
curl -s http://localhost:3000/login | grep -oi 'px-glass\|backdrop-filter' | head
```
Expected: build PASS; CSS contains the glass rule. Manually open `/login` in a browser; confirm focus ring on the email field is a dark high-contrast ring (Tab to it). Kill dev server.

- [ ] **Step 7: Commit**

```bash
git add app/globals.css
git commit -m "feat(app-theme): token sweep, glass utility, high-contrast focus ring, tabular nums

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `AppShell` — glass chrome + white content

**Files:**
- Modify: `components/dashboard/AppShell.tsx` (root wrapper, `<aside>` ~306-326, `<header>` ~461-483, content div ~558-566)
- Modify: `app/globals.css` (`@layer base` body backdrop)

**Mode:** CSS/structural (verify by serving + visual check).

> Decision: keep the existing continuous-corner mechanism (borderless chrome + concave corner painter + `rounded-tl-2xl` content). Foundation only (a) puts the gradient backdrop on the body, (b) makes rail+topbar glass, (c) flips content to solid white so it pops. The "even-gutter floating card" refinement is deferred to the page-polish plan (it interacts with sticky body-scroll + the `data-appshell-rail` master-detail asides).

- [ ] **Step 1: Body backdrop in `globals.css`**

In `@layer base`, change the `body` rule to paint the fixed gradient backdrop:

```css
  body {
    @apply text-foreground;
    background: var(--px-bg);
    background-image:
      radial-gradient(72% 58% at 6% 2%, rgba(172,215,220,0.34), transparent 60%),
      radial-gradient(64% 72% at 102% 104%, rgba(220,215,172,0.34), transparent 60%),
      radial-gradient(50% 50% at 60% -10%, rgba(220,172,175,0.16), transparent 70%),
      linear-gradient(135deg,#EFF2EE,#E7EEF1);
    background-attachment: fixed;
    font-feature-settings: "cv11", "ss01";
    -webkit-font-smoothing: antialiased;
  }
```
(Drop `bg-background` from the `@apply` so the gradient shows; keep `text-foreground`.)

- [ ] **Step 2: Root wrapper transparent**

`AppShell.tsx` root `<div>` (~301-304): change `style={{ background: "var(--px-bg)", … }}` → `style={{ background: "transparent", color: "var(--px-fg)" }}` so the body gradient shows through behind the glass.

- [ ] **Step 3: Glass nav rail**

`<aside>` (~319-325): add the `px-glass` class to its `className` and remove the solid `background: "var(--px-bg-2)"` from its `style` (let `.px-glass` provide the translucent fill). Keep `width`, `transition`, `zIndex`. Result: `className="... px-glass"` with style no longer setting background.

- [ ] **Step 4: Glass top bar + recolor corner painter**

`<header>` (~461-466): replace `style={{ background: "var(--px-bg-2)", zIndex: 9 }}` with the lighter glass via class — add `px-glass` to className and set style to `{ zIndex: 9 }`; then override its fill to `--px-glass-bg-2` with an inline style `background: "var(--px-glass-bg-2)"` AFTER the class (inline wins) OR add a one-off rule. Simplest: keep `px-glass` for blur+border, add inline `style={{ zIndex: 9, background: "var(--px-glass-bg-2)" }}`.
Corner painter (~480-482): change the two `var(--px-bg-2)` occurrences in the radial-gradient to `var(--px-glass-bg-2)`.

- [ ] **Step 5: White content panel**

Content div (~558-563): change `background: "var(--px-bg)"` → `background: "var(--px-surface)"`. Keep `border-l border-t rounded-tl-2xl` and `borderColor: var(--px-hairline)`. This makes the content a solid white surface that pops against the glass chrome.

- [ ] **Step 6: Serve + visual check**

`rm -rf .next && npm run dev &`, open `http://localhost:3000/` (log in if needed). Confirm: (a) no divider line between rail and topbar; (b) chrome reads as frosted glass over the pastel backdrop; (c) content is solid white and pops; (d) active nav shows the yellow left indicator. Kill dev server.

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/AppShell.tsx app/globals.css
git commit -m "feat(app-shell): fluid glass chrome over gradient backdrop, white content

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Button `loading` state (TDD)

**Files:**
- Test: `tests/components/Button.test.tsx` (create)
- Modify: `components/px/Button.tsx`

**Mode:** TDD.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/px/Button";

describe("Button loading", () => {
  it("disables the button and shows a spinner when loading", () => {
    render(<Button loading>Save</Button>);
    const btn = screen.getByRole("button", { name: /save/i });
    expect(btn).toBeDisabled();
    expect(btn.querySelector("svg")).not.toBeNull();
  });

  it("is not disabled when not loading", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: /save/i })).not.toBeDisabled();
  });

  it("keeps an explicit disabled even when not loading", () => {
    render(<Button disabled>Save</Button>);
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `npm run test -- Button.test`
Expected: FAIL (`loading` prop not supported; no svg rendered).

- [ ] **Step 3: Implement `loading` in `components/px/Button.tsx`**

Add `loading?: boolean` to the props type. In the component, compute `disabled = rest.disabled || loading`, and render a spinner SVG before children when loading:

```tsx
export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}
// …inside forwardRef destructure: add `loading = false, disabled, children,`
  const isDisabled = disabled || loading;
  return (
    <button
      ref={ref}
      type={type}
      className={cn(variantClass, sizeClass, className)}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && (
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
          className="px-spin" aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-6.2-8.6" />
        </svg>
      )}
      {children}
    </button>
  );
```
Add the spin keyframe to `app/globals.css`:
```css
@keyframes px-spin { to { transform: rotate(360deg); } }
.px-spin { animation: px-spin 0.7s linear infinite; }
```
(Loading spinners are the allowed exception to the reduced-motion rule, but guard anyway:)
```css
@media (prefers-reduced-motion: reduce) { .px-spin { animation-duration: 1.4s; } }
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `npm run test -- Button.test`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add components/px/Button.tsx app/globals.css tests/components/Button.test.tsx
git commit -m "feat(px-button): loading state (spinner + disabled + aria-busy)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Badge `dot` + `human` variant (TDD)

**Files:**
- Test: `tests/components/Badge.test.tsx` (create)
- Modify: `components/px/Badge.tsx`
- Modify: `app/globals.css` (add `.px-badge.human`, `.px-badge.neutral`)

**Mode:** TDD.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/px/Badge";

describe("Badge", () => {
  it("applies the human variant class", () => {
    render(<Badge variant="human">Borderline</Badge>);
    expect(screen.getByText("Borderline")).toHaveClass("px-badge", "human");
  });

  it("renders a dot when dot is set (color is not the only signal)", () => {
    const { container } = render(<Badge variant="ok" dot>Strong</Badge>);
    expect(container.querySelector(".px-dot")).not.toBeNull();
  });

  it("renders no dot by default", () => {
    const { container } = render(<Badge variant="ok">Strong</Badge>);
    expect(container.querySelector(".px-dot")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `npm run test -- Badge.test`
Expected: FAIL (`human` not in variant map; `dot` prop unsupported).

- [ ] **Step 3: Implement in `components/px/Badge.tsx`**

Add `human` and `neutral` to `BadgeVariant` and `VARIANT_MAP` (`human: "human"`, `neutral: "neutral"`), add `dot?: boolean` to props, and render a leading `<span className="px-dot" aria-hidden />` + wrap children when `dot`:

```tsx
export type BadgeVariant =
  | "default" | "primary" | "ok" | "caution" | "danger"
  | "ai" | "human" | "neutral" | "secondary" | "destructive";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
}

const VARIANT_MAP: Record<BadgeVariant, string> = {
  default: "", secondary: "", primary: "primary", ok: "ok",
  caution: "caution", danger: "danger", destructive: "danger",
  ai: "ai", human: "human", neutral: "neutral",
};

export function Badge({ variant = "default", dot = false, className, children, ...rest }: BadgeProps) {
  return (
    <span className={cn("px-badge", VARIANT_MAP[variant], className)} {...rest}>
      {dot && <span className="px-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
```

Add the CSS variants in `app/globals.css` next to the other `.px-badge.*`:
```css
.px-badge.human   { background: var(--px-human-bg); border-color: var(--px-human-line); color: var(--px-human); }
.px-badge.neutral { background: var(--px-neutral-cat-bg); border-color: var(--px-neutral-cat-line); color: var(--px-neutral-cat); }
```
Also recolor the existing `.px-badge.ok/.caution/.danger/.ai` (they already use `--px-*`/`--px-*-bg`/`--px-*-line`, which now resolve to the daylight triads — confirm they reference `-bg`/`-line`/role vars, not removed names).

- [ ] **Step 4: Run the test — verify it passes**

Run: `npm run test -- Badge.test`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add components/px/Badge.tsx app/globals.css tests/components/Badge.test.tsx
git commit -m "feat(px-badge): human/neutral variants + optional dot (color-not-alone)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Foundation verification gate

**Files:** none (verification only).

**Mode:** gate.

- [ ] **Step 1: Lint, type-check, full test suite, build**

```bash
npm run lint && npm run type-check && npm run test && npm run build
```
Expected: all PASS, zero errors/failures. If any pre-existing test asserts a removed token/class, fix it to the new equivalent (most likely none — only `tests/setup.ts` and `tests/components/SendInviteDialog.test.tsx` reference touched terms, and neither asserts colors).

- [ ] **Step 2: Cross-surface isolation check**

```bash
git status --porcelain frontend/session frontend/admin   # must be empty
grep -i livekit frontend/app/package.json || echo "clean: no livekit in app"
```
Expected: no changes leaked into `session`/`admin`; no livekit dep.

- [ ] **Step 3: Served-frontend visual pass (clean restart)**

```bash
rm -rf .next && npm run dev &   # wait for Ready
```
In a browser at 1280px and 1440px, walk: `/login`, `/` (dashboard), one list page (`/candidates`). Confirm: Urbanist applied; glass chrome + white content; yellow accent only on active/primary; pills show color+label (+dot where used); keyboard focus is the dark high-contrast ring; no beige remnants. Kill dev server.

- [ ] **Step 4: Final commit (if any fixes were made in Step 1)**

```bash
git add -A frontend/app
git commit -m "chore(app-theme): foundation verification fixes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes (done by planner)

- **Spec coverage:** §2 design language → Tasks 1,3,4. §3 color system (structural/glass/semantic triads/`--c-*`/shadcn) → Task 1. §4 typography → Task 2. §5 glass shell + focus ring → Tasks 3,4. §6 primitive retune → Tasks 3 (CSS), 5 (Button loading), 6 (Badge dot/human); remaining primitive recolors (Input/Select/Dialog/Tooltip/Toaster/Skeleton) are token-driven and land automatically via Task 1+3 (their `.px-*` CSS already references `--px-*` vars). §9 a11y gates → Tasks 3 (focus, glass fallback), 6 (color-not-alone), 7 (contrast/served check). §10 verification → Task 7. **§7 page-polish → deliberately a separate follow-up plan** (stated in header).
- **Placeholder scan:** structural/glass/semantic/shadcn token values are exact; `--c-*` ramps give exact anchors + an explicit interpolation rule (matches the warm-light precedent) — not a TODO.
- **Type consistency:** `ThemeName` "daylight" used in Task 1 matches `brand.theme` in Task 1. `BadgeVariant` additions (`human`,`neutral`) consistent between Badge.tsx and the new `.px-badge.*` CSS. `--font-serif` aliased to `--font-sans` in layout (Task 2) so `--font-heading`/`.px-serif`/dialog-title references stay valid (Tasks 2,3).
- **Known follow-ups for the page-polish plan:** even-gutter floating content card; master-detail aside realignment to the glass rail edge (`data-appshell-rail`); per-page pastel category application; tables (aria-sort, overflow-x, bulk actions, `.px-num`); chart palette wiring on Reports.
