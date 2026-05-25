# Frontend (App) — Brand & Theme Centralization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the recruiter app's name + logo editable from one file (`lib/brand.ts`) and its entire visual theme editable from one file (`app/theme.css`), and rebrand the visible product from "ProjectX" to "BinQle.ai".

**Architecture:** Three new units — `lib/brand.ts` (typed brand/look config), `app/theme.css` (all design tokens, one block per theme, palette utilities re-pointed through per-theme `--c-*` references so a theme swap recolors the whole app), `components/px/BrandLogo.tsx` (`<BrandLogo>` wordmark + `<BrandMark>` square mark, derived from the BinQle wordmark asset). Existing hardcoded brand strings and inline logo SVGs are rewired to read from these. Warm-light values stay byte-for-byte identical; designing new palettes is a later phase.

**Tech Stack:** Next.js 16 (App Router), TypeScript strict, Tailwind CSS v4 (`@theme inline` + CSS custom properties, no `tailwind.config`), `@base-ui-components/react`, Vitest + Testing Library + jsdom, PIL/ImageMagick for one-time asset derivation.

**Spec:** `docs/superpowers/specs/2026-05-25-frontend-app-brand-theme-centralization-design.md`

**Working dir for all commands:** `frontend/app/` unless a path says otherwise.

> **Branching note for execution:** `main` currently has unrelated uncommitted proctoring changes. Execute this plan on a dedicated branch/worktree (e.g. `git switch -c feat/app-brand-theme`) and use **explicit `git add <paths>`** in every commit — never `git add -A` — so the proctoring WIP is never swept in.

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `frontend/app/public/brand/binqle-wordmark.png` | Wide wordmark asset (trimmed, web-sized) | Create |
| `frontend/app/public/brand/binqle-mark.png` | Square mark derived from the "Q" (256×256) | Create |
| `frontend/app/app/favicon.ico` | Favicon regenerated from the mark | Overwrite |
| `frontend/app/public/projectx-logo.svg` | Dead asset | Delete |
| `frontend/app/lib/brand.ts` | Brand identity + active theme/density (single source) | Create |
| `frontend/app/app/theme.css` | All design tokens, one block per theme + density | Create |
| `frontend/app/app/globals.css` | Tailwind plumbing + `.px-*` utilities; palette refs re-pointed | Modify |
| `frontend/app/components/px/BrandLogo.tsx` | `<BrandLogo>` + `<BrandMark>` | Create |
| `frontend/app/components/px/index.ts` | Barrel export | Modify |
| `frontend/app/tests/components/BrandLogo.test.tsx` | Component tests | Create |
| `frontend/app/app/layout.tsx` | Metadata + `data-px-theme`/`-density` from brand | Modify |
| `frontend/app/components/dashboard/AppShell.tsx` | Sidebar brand → `<BrandMark>`/`<BrandLogo>` | Modify |
| `frontend/app/app/(auth)/login/page.tsx` | Login brand → `<BrandLogo>` + subtitle | Modify |
| `frontend/app/app/suspended/page.tsx` | Copy → `brand.name` | Modify |
| 6 integrations/team/org-units files | Prose "ProjectX" → `brand.shortName` | Modify |
| `frontend/app/CLAUDE.md` | Document new structure + session divergence | Modify |

---

## Task 1: Derive and place the BinQle brand assets

**Files:**
- Create: `frontend/app/public/brand/binqle-wordmark.png`
- Create: `frontend/app/public/brand/binqle-mark.png`
- Overwrite: `frontend/app/app/favicon.ico`
- Delete: `frontend/app/public/projectx-logo.svg`
- Source (read-only): `assets/BinQle_Logo_3840x2160_Color-Transparent.png` (repo root)

No automated test — this is a one-time asset derivation verified by viewing the outputs.

- [ ] **Step 1: Make the target dir**

Run (from repo root):
```bash
mkdir -p frontend/app/public/brand
```

- [ ] **Step 2: Trim + downscale the wordmark, and measure its trimmed dimensions**

Run (from repo root):
```bash
python3 - <<'PY'
from PIL import Image
src = Image.open("assets/BinQle_Logo_3840x2160_Color-Transparent.png").convert("RGBA")
# Trim fully-transparent margins using the alpha channel bounding box.
bbox = src.split()[3].getbbox()
trimmed = src.crop(bbox)
# Downscale to ~960px wide for web, preserving aspect + alpha.
target_w = 960
ratio = target_w / trimmed.width
out = trimmed.resize((target_w, max(1, round(trimmed.height * ratio))), Image.LANCZOS)
out.save("frontend/app/public/brand/binqle-wordmark.png")
print("WORDMARK_DIMS", out.width, out.height)   # <-- record these for brand.ts
PY
```
Expected: prints `WORDMARK_DIMS 960 <h>`. **Record `960` and the printed height** — they go into `brand.ts` in Task 2.

- [ ] **Step 3: Explore where the "Q" sits (so the mark crop is centered correctly)**

Run (from repo root):
```bash
python3 - <<'PY'
from PIL import Image
im = Image.open("assets/BinQle_Logo_3840x2160_Color-Transparent.png").convert("RGBA")
bbox = im.split()[3].getbbox()
im = im.crop(bbox)
W, H = im.size
px = im.load()
# "Colorful/bright" = the cyan->purple Q ring (navy ink is dark). Print a coarse
# column histogram of bright opaque pixels so we can see the Q's column cluster.
cols = [0]*20
for x in range(W):
    c = 0
    for y in range(0, H, 4):
        r,g,b,a = px[x,y]
        if a > 40 and (r+g+b)/3 > 95:   # bright = gradient ring, not navy text
            c += 1
    cols[int(x/W*20)] += c
print("W,H", W, H)
for i,v in enumerate(cols):
    print(f"{i*5:>3}%-{(i+1)*5:>3}%  {'#'*(v//20)} ({v})")
PY
```
Expected: a histogram; the tall bars mark the Q's horizontal band. Note the percentage range where the Q is densest (e.g. `~50%–70%`) → its center fraction `f` (e.g. `0.60`).

- [ ] **Step 4: Crop a square mark centered on the Q (256×256)**

Run (from repo root), setting `F` to the center fraction from Step 3 (start with `0.60`):
```bash
python3 - <<'PY'
from PIL import Image
F = 0.60   # <-- center-of-Q fraction from Step 3 histogram; nudge if needed
im = Image.open("assets/BinQle_Logo_3840x2160_Color-Transparent.png").convert("RGBA")
im = im.crop(im.split()[3].getbbox())
W, H = im.size
side = H                      # square = full glyph height
cx = int(W * F)
left = max(0, min(W - side, cx - side // 2))
mark = im.crop((left, 0, left + side, side))
mark = mark.resize((256, 256), Image.LANCZOS)
mark.save("frontend/app/public/brand/binqle-mark.png")
print("mark saved; left=", left, "side=", side)
PY
```

- [ ] **Step 5: View both outputs and confirm the mark cleanly frames the "Q"**

Use the Read tool on `frontend/app/public/brand/binqle-mark.png` and `frontend/app/public/brand/binqle-wordmark.png`.
Expected: wordmark = full "BinQle" lockup, tight margins; mark = the gradient "Q" centered in a square.
If the mark clips the Q or includes the "l/e", **re-run Step 4 with an adjusted `F`** (lower = left, higher = right) until it frames the Q.

- [ ] **Step 6: Regenerate the favicon from the mark**

Run (from repo root):
```bash
magick frontend/app/public/brand/binqle-mark.png -background none \
  -define icon:auto-resize=16,32,48,64 frontend/app/app/favicon.ico
```
Expected: no error; `frontend/app/app/favicon.ico` updated.

- [ ] **Step 7: Delete the dead logo**

Run (from repo root):
```bash
git rm frontend/app/public/projectx-logo.svg
```
Expected: `rm 'frontend/app/public/projectx-logo.svg'`.

- [ ] **Step 8: Commit**

Run (from repo root):
```bash
git add frontend/app/public/brand/binqle-wordmark.png \
        frontend/app/public/brand/binqle-mark.png \
        frontend/app/app/favicon.ico
git commit -m "feat(app-brand): add BinQle wordmark + derived mark + favicon, drop dead logo

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Create `lib/brand.ts`

**Files:**
- Create: `frontend/app/lib/brand.ts`

- [ ] **Step 1: Write the config module**

Use the wordmark height recorded in Task 1 Step 2 for `wordmark.height` (replace `WORDMARK_HEIGHT`).

```ts
// lib/brand.ts
// Single source of truth for product identity and the active look.
// Change the name/logo here; change colors in app/theme.css.

export type ThemeName = "warm-light"; // add new names here as themes are added to app/theme.css
export type DensityName = "compact" | "comfortable" | "spacious";

export interface LogoAsset {
  /** Path under public/ */
  src: string;
  /** Intrinsic pixel dimensions (post-trim) — required by next/image. */
  width: number;
  height: number;
}

export interface BrandConfig {
  /** Full product name — browser tab title + prose. */
  name: string;
  /** Compact name for tight inline contexts. */
  shortName: string;
  /** Metadata description. */
  tagline: string;
  /** Login screen sub-headline. */
  loginSubtitle: string;
  logo: { wordmark: LogoAsset; mark: LogoAsset };
  /** Active look. Must match a [data-px-theme="…"] block in app/theme.css. */
  theme: ThemeName;
  /** Active density. */
  density: DensityName;
}

export const brand = {
  name: "BinQle.ai",
  shortName: "BinQle",
  tagline: "AI Video Interview Platform",
  loginSubtitle: "Sign in to your recruiting dashboard",
  logo: {
    wordmark: { src: "/brand/binqle-wordmark.png", width: 960, height: WORDMARK_HEIGHT },
    mark: { src: "/brand/binqle-mark.png", width: 256, height: 256 },
  },
  theme: "warm-light",
  density: "comfortable",
} as const satisfies BrandConfig;
```

- [ ] **Step 2: Verify it type-checks**

Run (from `frontend/app`):
```bash
npm run type-check
```
Expected: PASS (zero errors). If `WORDMARK_HEIGHT` was left literal, this fails — replace it with the recorded integer.

- [ ] **Step 3: Commit**

Run (from repo root):
```bash
git add frontend/app/lib/brand.ts
git commit -m "feat(app-brand): add centralized brand config (lib/brand.ts)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Create `<BrandLogo>` + `<BrandMark>` (TDD)

**Files:**
- Create: `frontend/app/components/px/BrandLogo.tsx`
- Modify: `frontend/app/components/px/index.ts`
- Test: `frontend/app/tests/components/BrandLogo.test.tsx`

- [ ] **Step 1: Check the Next 16 `next/image` API before writing**

Run (from `frontend/app`):
```bash
ls node_modules/next/dist/docs/ 2>/dev/null; grep -rl "next/image" node_modules/next/dist/docs/ 2>/dev/null | head
```
Skim the image doc if present. Confirm the import is `import Image from "next/image"` and that `width`/`height`/`alt`/`src` props are accepted. (Per `AGENTS.md`, do not assume training-data API.)

- [ ] **Step 2: Write the failing test**

```tsx
// tests/components/BrandLogo.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { BrandLogo, BrandMark } from "@/components/px";
import { brand } from "@/lib/brand";

describe("BrandLogo", () => {
  it("renders the wordmark with the brand name as alt text", () => {
    render(<BrandLogo height={32} />);
    const img = screen.getByAltText(brand.name);
    expect(img).toBeInTheDocument();
  });
});

describe("BrandMark", () => {
  it("renders the square mark with the brand name as alt text", () => {
    render(<BrandMark size={26} />);
    const img = screen.getByAltText(brand.name);
    expect(img).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run (from `frontend/app`):
```bash
npm run test -- BrandLogo
```
Expected: FAIL — `BrandLogo`/`BrandMark` are not exported from `@/components/px`.

- [ ] **Step 4: Implement the components**

```tsx
// components/px/BrandLogo.tsx
import Image from "next/image";
import { brand } from "@/lib/brand";

/** Full wordmark lockup. Scales to the given pixel height, aspect preserved. */
export function BrandLogo({
  height = 32,
  className,
}: {
  height?: number;
  className?: string;
}) {
  const { src, width, height: ih } = brand.logo.wordmark;
  return (
    <Image
      src={src}
      width={width}
      height={ih}
      alt={brand.name}
      priority
      className={className}
      style={{ height, width: "auto" }}
    />
  );
}

/** Square mark (the gradient "Q"). For collapsed rails / tight spots. */
export function BrandMark({
  size = 26,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const { src, width, height } = brand.logo.mark;
  return (
    <Image
      src={src}
      width={width}
      height={height}
      alt={brand.name}
      priority
      className={className}
      style={{ width: size, height: size }}
    />
  );
}
```

- [ ] **Step 5: Add barrel exports**

In `components/px/index.ts`, add (next to the other exports):
```ts
export { BrandLogo, BrandMark } from "./BrandLogo";
```

- [ ] **Step 6: Run the test to verify it passes**

Run (from `frontend/app`):
```bash
npm run test -- BrandLogo
```
Expected: PASS (both tests). If next/image throws in jsdom over missing config, confirm `width`/`height` are passed (they are) — next/image renders a plain `<img>` in tests.

- [ ] **Step 7: Commit**

Run (from repo root):
```bash
git add frontend/app/components/px/BrandLogo.tsx \
        frontend/app/components/px/index.ts \
        frontend/app/tests/components/BrandLogo.test.tsx
git commit -m "feat(app-brand): add <BrandLogo> + <BrandMark> px primitives

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Split design tokens into `app/theme.css` and re-point palette utilities

This is a mechanical CSS refactor — no unit test; verified by `build` + a byte-identical visual check. **Do not change any color values**; only relocate and re-reference.

**Files:**
- Create: `frontend/app/app/theme.css`
- Modify: `frontend/app/app/globals.css`

- [ ] **Step 1: Create `app/theme.css` with the relocated look-tokens**

Move (cut) these from `globals.css` into a new `app/theme.css`, preserving values verbatim:
- The entire `:root, [data-px-theme="warm-light"] { … }` block (currently ~lines 252–360: all `--px-*` tokens, the shadcn semantic mapping `--background`/`--primary`/… , radii, shadows, motion).
- The three density modifier rules (`[data-px-density="compact"|"comfortable"|"spacious"]`).

Then, **inside the `:root, [data-px-theme="warm-light"]` block**, add the per-theme palette ramp definitions `--c-*` — one for every literal `--color-*` override currently living in the `@theme inline` block (the contiguous run from `--color-white: #FCFAF6;` through `--color-fuchsia-600: #5F3C78;`). The rule is 1:1: `--color-<key>: <hex>;` → `--c-<key>: <hex>;`.

Worked example (zinc + white), apply the identical pattern to every family — `white`, `zinc`, `neutral`, `stone`, `slate`, `gray`, `red`, `rose`, `green`, `emerald`, `teal`, `blue`, `sky`, `indigo`, `amber`, `yellow`, `orange`, `purple`, `violet`, `fuchsia` (use exactly the same shade keys + hex values that exist today):
```css
  /* Tailwind named-palette ramps — per theme, referenced from globals.css @theme */
  --c-white: #FCFAF6;
  --c-zinc-50: #F6F2EC;
  --c-zinc-100: #EEE8DE;
  --c-zinc-200: #E5DFD3;
  --c-zinc-300: #C5BFB3;
  --c-zinc-400: #9A9388;
  --c-zinc-500: #6B6458;
  --c-zinc-600: #3A342B;
  --c-zinc-700: #1E1B16;
  --c-zinc-800: #1E1B16;
  --c-zinc-900: #1E1B16;
  --c-zinc-950: #0F0D0A;
  /* …neutral / stone / slate / gray / red / rose / green / emerald / teal /
       blue / sky / indigo / amber / yellow / orange / purple / violet / fuchsia… */
```

Header the file so the "add a theme" path is obvious:
```css
/* ─────────────────────────────────────────────────────────
   Design tokens — the LOOK of the app, one block per theme.
   To add a theme: copy the warm-light block below, rename the
   selector to [data-px-theme="<name>"], change the values, then
   add "<name>" to ThemeName in lib/brand.ts and set brand.theme.
   ───────────────────────────────────────────────────────── */
```

- [ ] **Step 2: Re-point the `@theme inline` palette overrides in `globals.css` to the `--c-*` references**

In `globals.css`, for every literal palette line in the `@theme inline` block (the run `--color-white` … `--color-fuchsia-600`), change the value from the hex literal to the matching `var(--c-*)`. Worked example:
```css
  /* before */
  --color-white: #FCFAF6;
  --color-zinc-50: #F6F2EC;
  /* after */
  --color-white: var(--c-white);
  --color-zinc-50: var(--c-zinc-50);
```
Apply to **every** `--color-<key>` in that run. Leave untouched: the `--color-px-*` mappings, the shadcn `--color-*: var(--…)` mappings, `--radius-*`, `--font-*`, `--breakpoint-3xl` (these already reference variables or are config).

- [ ] **Step 3: Import `theme.css` from `globals.css`**

At the top of `globals.css`, immediately after `@import "tailwindcss";`, add:
```css
@import "./theme.css";
```
Confirm `globals.css` still contains: the `@theme inline` block (now using `var(--c-*)`), the `@layer base` block, and all `.px-*` utility classes — and **no longer** contains the `:root`/`[data-px-theme]`/density blocks (those now live in `theme.css`).

- [ ] **Step 4: Verify the build compiles and tokens resolve**

Run (from `frontend/app`):
```bash
npm run build
```
Expected: build succeeds. If Tailwind errors that `@theme inline` cannot resolve `var(--c-*)` defined in an imported file, fall back: keep the `@theme inline` block as-is in `globals.css` but move only the `--c-*` *definitions* and `--px-*`/shadcn definitions into `theme.css` (the `@theme` mapping must stay in `globals.css`; the value definitions can live in the imported file). Re-run `npm run build`.

- [ ] **Step 5: Verify the look is unchanged (no value drifted)**

Run (from `frontend/app`), start the dev server in the background and spot-check rendered CSS variables resolve to the warm-light values:
```bash
npm run dev &
sleep 6
curl -s http://localhost:3000/login | grep -o 'data-px-theme="[^"]*"' | head -1
```
Expected: `data-px-theme="warm-light"`. Then open `http://localhost:3000/login` in the browser (or use the run skill) and confirm the page looks identical to before (warm off-white bg, teal accent). Stop the dev server when done (`kill %1`).

- [ ] **Step 6: Commit**

Run (from repo root):
```bash
git add frontend/app/app/theme.css frontend/app/app/globals.css
git commit -m "refactor(app-theme): split design tokens into theme.css; per-theme palette refs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire `app/layout.tsx` to `brand.ts`

**Files:**
- Modify: `frontend/app/app/layout.tsx`

- [ ] **Step 1: Replace hardcoded metadata + html attributes**

Add the import:
```ts
import { brand } from "@/lib/brand";
```
Change `metadata`:
```ts
export const metadata: Metadata = {
  title: brand.name,
  description: brand.tagline,
};
```
Change the `<html>` attributes from the hardcoded literals to:
```tsx
      data-px-theme={brand.theme}
      data-px-density={brand.density}
```

- [ ] **Step 2: Type-check + build**

Run (from `frontend/app`):
```bash
npm run type-check && npm run build
```
Expected: PASS. Tab title is now "BinQle.ai".

- [ ] **Step 3: Commit**

Run (from repo root):
```bash
git add frontend/app/app/layout.tsx
git commit -m "feat(app-brand): drive layout metadata + theme/density from brand config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Wire the sidebar brand in `AppShell.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/AppShell.tsx` (brand block at ~lines 326–369)

- [ ] **Step 1: Import the brand components**

Add near the top imports:
```ts
import { BrandLogo, BrandMark } from "@/components/px";
```

- [ ] **Step 2: Replace the inline play-SVG box + "ProjectX" text**

In the "Brand" block, replace the teal SVG box `<div>` and the `ProjectX` text node with the logo. Render `<BrandMark>` always (it's the compact identity), and the `<BrandLogo>` only when expanded; keep `orgContext` and the collapse button intact:
```tsx
        {/* Brand */}
        <div
          className="flex flex-shrink-0 items-center gap-2.5 px-3.5"
          style={{ height: 52 }}
        >
          {collapsed ? (
            <BrandMark size={26} />
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <BrandLogo height={22} />
              {orgContext && (
                <div
                  className="truncate text-[10.5px] leading-tight"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  {orgContext}
                </div>
              )}
            </div>
          )}
          {!collapsed && (
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              title="Collapse"
              aria-label="Collapse sidebar"
              className="flex h-[22px] w-[22px] cursor-pointer items-center justify-center rounded border-none bg-transparent"
              style={{ color: "var(--px-fg-3)" }}
            >
              <ShIcon d={NI.chevL} size={13} />
            </button>
          )}
        </div>
```

- [ ] **Step 3: Type-check + lint**

Run (from `frontend/app`):
```bash
npm run type-check && npm run lint
```
Expected: PASS. (The inline `<svg>` brand mark and the `ProjectX` literal are gone.)

- [ ] **Step 4: Visually verify the sidebar (expanded + collapsed)**

Run (from `frontend/app`): `npm run dev &`, then open the dashboard, confirm the BinQle wordmark shows expanded and the square mark shows when collapsed. `kill %1` when done.

- [ ] **Step 5: Commit**

Run (from repo root):
```bash
git add frontend/app/components/dashboard/AppShell.tsx
git commit -m "feat(app-brand): render BinQle logo in the sidebar (mark when collapsed)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Wire the login brand in `(auth)/login/page.tsx`

**Files:**
- Modify: `frontend/app/app/(auth)/login/page.tsx` (brand block ~lines 80–99)

- [ ] **Step 1: Import the brand + component**

Add to the imports:
```ts
import { BrandLogo } from "@/components/px";
import { brand } from "@/lib/brand";
```

- [ ] **Step 2: Replace the inline SVG circle + "ProjectX" `<h1>` + subtitle**

Replace the brand header block with:
```tsx
      <div className="mb-8 text-center">
        <div className="mb-4 flex justify-center">
          <BrandLogo height={40} />
        </div>
        <p className="mt-1 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
          {brand.loginSubtitle}
        </p>
      </div>
```

- [ ] **Step 3: Type-check + lint**

Run (from `frontend/app`):
```bash
npm run type-check && npm run lint
```
Expected: PASS.

- [ ] **Step 4: Commit**

Run (from repo root):
```bash
git add "frontend/app/app/(auth)/login/page.tsx"
git commit -m "feat(app-brand): render BinQle wordmark on the login screen

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Replace remaining "ProjectX" prose with `brand.shortName`

**Files (modify):**
- `frontend/app/app/suspended/page.tsx` (~line 69)
- `frontend/app/app/(dashboard)/settings/team/page.tsx` (~line 441)
- `frontend/app/app/(dashboard)/settings/integrations/page.tsx` (~line 28)
- `frontend/app/app/(dashboard)/settings/integrations/connect/page.tsx` (~line 22)
- `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx` (~lines 256, 311)
- `frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx` (~line 153)
- `frontend/app/components/settings/integrations/CeipalConnectionForm.tsx` (~line 130)
- `frontend/app/app/(dashboard)/settings/org-units/[unitId]/Sidebar.tsx` (~line 183)

- [ ] **Step 1: In each file, import brand and replace the literal**

For each file, add `import { brand } from "@/lib/brand";` (if not already importing it) and replace the visible word `ProjectX` with `{brand.shortName}` in JSX, or `${brand.shortName}` inside template strings (e.g. the team-page `Deactivate ${m.email}? They will lose access to ProjectX.`).

Suspended page uses `brand.name` (full) since it's the workspace-level message:
```tsx
Your organization&apos;s {brand.name} workspace has been suspended.
```
All others use `brand.shortName`.

- [ ] **Step 2: Confirm no visible "ProjectX" remains (API field names excluded)**

Run (from `frontend/app`):
```bash
grep -rni "projectx" --include="*.ts" --include="*.tsx" app components lib | grep -vi "projectx_stage_id"
```
Expected: **zero matches.** (`projectx_stage_id` in `lib/api/ats.ts` is the only allowed remaining hit and is filtered out here.)

- [ ] **Step 3: Type-check + lint + test**

Run (from `frontend/app`):
```bash
npm run type-check && npm run lint && npm run test
```
Expected: all PASS.

- [ ] **Step 4: Commit**

Run (from repo root):
```bash
git add frontend/app/app/suspended/page.tsx \
        "frontend/app/app/(dashboard)/settings/team/page.tsx" \
        "frontend/app/app/(dashboard)/settings/integrations/page.tsx" \
        "frontend/app/app/(dashboard)/settings/integrations/connect/page.tsx" \
        "frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx" \
        frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx \
        frontend/app/components/settings/integrations/CeipalConnectionForm.tsx \
        "frontend/app/app/(dashboard)/settings/org-units/[unitId]/Sidebar.tsx"
git commit -m "feat(app-brand): replace remaining ProjectX prose with brand config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Update `frontend/app/CLAUDE.md`

**Files:**
- Modify: `frontend/app/CLAUDE.md`

- [ ] **Step 1: Update the "shared by duplication with frontend/session" table**

Remove the `public/projectx-logo.svg` row. Append a note under the table recording the deliberate divergence:
> **Divergence (2026-05-25):** the recruiter app was rebranded to **BinQle** and its design tokens were extracted to `app/theme.css` + `lib/brand.ts`. `frontend/session` was intentionally **not** updated in that pass (separate brand surface, later phase). The "Shadcn → px token mapping" row above now refers to `app/theme.css` (the values) + `app/globals.css` (the `@theme` mapping) in the recruiter app.

- [ ] **Step 2: Update the Directory Structure block**

- Fix the `layout.tsx` comment: `← Root layout (Geist fonts, zinc-50 bg)` → `← Root layout (Inter/Fraunces/JetBrains fonts; theme + density from lib/brand.ts)`.
- Fix the `globals.css` comment: `← Tailwind v4 import only + @theme tokens` → `← Tailwind import, @theme mapping, .px-* utilities (imports ./theme.css)`.
- Add new entries: `app/theme.css ← all design tokens, one block per theme + density`; under `lib/`: `brand.ts ← name/logo/tagline + active theme/density (single source)`; under `components/px/`: note `BrandLogo.tsx (<BrandLogo>, <BrandMark>)`; add `public/brand/ ← binqle-wordmark.png + binqle-mark.png`.

- [ ] **Step 3: Add a "Branding & Theming" subsection (under Tailwind Standards)**

```markdown
### Branding & Theming — single source of truth

- **Name / logo / tagline / active look:** `lib/brand.ts`. Change the product
  name, logo assets, tagline, or the active `theme`/`density` here — nowhere else.
  Visible product name today: **BinQle.ai** (`brand.name`) / **BinQle** (`brand.shortName`).
- **Colors / radii / shadows / density:** `app/theme.css`. One block per theme
  (`[data-px-theme="<name>"]`). To add a theme: copy the `warm-light` block,
  rename the selector, change values, add the name to `ThemeName` in `lib/brand.ts`,
  set `brand.theme`. Tailwind named-palette utilities (`bg-zinc-*`, `text-red-*`, …)
  resolve through per-theme `--c-*` variables, so a theme swap recolors the whole app.
- **Logo in components:** `<BrandLogo>` (wordmark) and `<BrandMark>` (square mark)
  from `@/components/px`. Do not hardcode logo SVGs or the product name in components.
- `app/globals.css` holds only Tailwind plumbing (`@theme inline` mapping) + the
  `.px-*` utility classes, and `@import "./theme.css"`.
```

- [ ] **Step 4: Commit**

Run (from repo root):
```bash
git add frontend/app/CLAUDE.md
git commit -m "docs(app): document brand.ts / theme.css / BrandLogo + session divergence

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Final full-suite verification

No new files. Confirms the whole change is green and brand-clean.

- [ ] **Step 1: Run the full gate**

Run (from `frontend/app`):
```bash
npm run lint && npm run type-check && npm run test && npm run build
```
Expected: all PASS, zero errors.

- [ ] **Step 2: Confirm brand-string cleanliness**

Run (from `frontend/app`):
```bash
grep -rni "projectx" --include="*.ts" --include="*.tsx" app components lib | grep -vi "projectx_stage_id" || echo "CLEAN"
```
Expected: `CLEAN`.

- [ ] **Step 3: Smoke-test the running app**

Run (from `frontend/app`): `npm run dev &`, then open `http://localhost:3000/login` (BinQle wordmark + subtitle), sign in / open the dashboard (sidebar wordmark; collapse → mark), and confirm the warm-light look is unchanged. Check the browser tab title reads **BinQle.ai**. `kill %1` when done.

- [ ] **Step 4: Theme-swap smoke proof (optional but recommended)**

Temporarily edit one warm-light value in `app/theme.css` (e.g. `--px-accent: #2B6CB8;`), reload the dashboard, confirm accents (sidebar active bar, primary buttons) recolor, then **revert the edit**. This proves the single-file theme switch works end to end. Do not commit the temporary edit.

---

## Self-review notes (author)

- **Spec coverage:** brand.ts (T2), theme.css split + palette re-point (T4), BrandLogo/BrandMark (T3), asset pipeline incl. favicon + dead-svg delete (T1), all 11 name sites (layout T5, AppShell T6, login T7, 8 prose files T8), tests (T3), docs + divergence (T9), verification incl. theme-swap proof (T10). All spec sections map to a task.
- **Deviation from spec:** spec floated a dedicated AppShell composition test; this plan verifies the sidebar wiring via type-check + the dev-server smoke test instead of a brittle mount test (AppShell pulls `usePathname`/`useRouter`/supabase). BrandLogo/BrandMark get real unit tests. Flagged for reviewer.
- **Type consistency:** `BrandConfig`/`LogoAsset`/`ThemeName`/`DensityName` and `brand.logo.wordmark|mark` are used identically across T2/T3/T5/T6/T7/T8.
- **Placeholder scan:** the only intentional fill-in is `WORDMARK_HEIGHT` in T2 (measured in T1 S2) and the crop fraction `F` in T1 (measured in T1 S3) — both have explicit measurement steps, not "TBD".
