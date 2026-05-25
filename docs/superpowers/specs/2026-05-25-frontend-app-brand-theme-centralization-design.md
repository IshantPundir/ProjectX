# Frontend (App) — Brand & Theme Centralization

**Date:** 2026-05-25
**Surface:** `frontend/app/` (recruiter dashboard ONLY)
**Status:** Approved design — ready for implementation plan

---

## Problem

The recruiter dashboard's identity is scattered and its look is hard to change:

1. **Name** — `"ProjectX"` is hardcoded as a visible string in 11 places (tab title, sidebar, login heading, suspended copy, 7 integrations/team/org-units prose strings).
2. **Logo** — there is no real logo wiring. The sidebar and login each render an inline "play-triangle in a teal box" SVG. `public/projectx-logo.svg` exists but is imported nowhere (dead). The new brand asset (`assets/BinQle_Logo_3840x2160_Color-Transparent.png`) is a wide 16:9 **wordmark** that won't fit a collapsed 26px sidebar slot or a favicon.
3. **Theme** — `app/globals.css` (986 lines) already drives the look through CSS custom properties and is built around a `[data-px-theme="warm-light"]` attribute (set once in `app/layout.tsx`), so the multi-theme *mechanism* is half-present. But the palette values, the Tailwind named-palette overrides, the shadcn token mapping, and ~600 lines of `.px-*` utility classes are all mixed in one file, so "change the look" is not a clean, obvious edit. Critically, the Tailwind named-palette overrides (`--color-zinc-50: #F6F2EC`, etc.) are **literal hex frozen inside `@theme`**, so even a working theme swap would leave every raw `bg-zinc-*` / `text-red-*` utility stuck on the warm-beige values.

**Goal:** one obvious file to change the **name + logo**, and one obvious file to change the **entire look** — so future rebrands and theme work are shallow edits, not code spelunking.

---

## Decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Visible product name | **Switch to `BinQle.ai` now.** Centralized so renaming/reverting is a one-line change. |
| Config shape | **One file per concern.** `lib/brand.ts` (name/logo/tagline/active-look) + `app/theme.css` (all design tokens, per-theme) + `components/px/BrandLogo.tsx`. Idiomatic for Tailwind v4; zero runtime cost. (Rejected: a single TS file with runtime CSS-variable injection — fights Tailwind v4's static `@theme` and adds SSR/FOUC risk.) |
| Logo mark | **Derive a square mark** from the wordmark's gradient "Q" via PIL/ImageMagick. No new asset required from the user. |
| New palette / design language | **Out of scope.** This phase only *reorganizes* so the look is swappable; warm-light values stay byte-for-byte identical. Designing new themes is the next phase. |
| `frontend/session` | **Deliberately diverges.** Only the recruiter app is rebranded now; the candidate surface is a separate brand surface and a later phase. Documented, not touched. |

---

## Architecture

Three new units, each with one clear responsibility:

### 1. `lib/brand.ts` — brand single-source-of-truth

A typed config object. The product identity and the *active* look are chosen here and nowhere else.

```ts
export type ThemeName = "warm-light";          // grows as themes are added
export type DensityName = "compact" | "comfortable" | "spacious";

export interface LogoAsset {
  src: string;     // public path
  width: number;   // intrinsic px (post-trim)
  height: number;  // intrinsic px (post-trim)
}

export interface BrandConfig {
  name: string;          // full product name — tab title + prose
  shortName: string;     // tight contexts / inline prose
  tagline: string;       // metadata description
  loginSubtitle: string; // login screen sub-headline
  logo: { wordmark: LogoAsset; mark: LogoAsset };
  theme: ThemeName;      // active look — ONE line switches the whole app
  density: DensityName;  // active density
}

export const brand = {
  name: "BinQle.ai",
  shortName: "BinQle",
  tagline: "AI Video Interview Platform",
  loginSubtitle: "Sign in to your recruiting dashboard",
  logo: {
    wordmark: { src: "/brand/binqle-wordmark.png", width: 0, height: 0 }, // dims filled from trimmed asset
    mark:     { src: "/brand/binqle-mark.png",     width: 256, height: 256 },
  },
  theme: "warm-light",
  density: "comfortable",
} as const satisfies BrandConfig;
```

- Server-importable (plain module, no React) so `app/layout.tsx` (server component) reads it directly.
- `theme` / `density` move *out* of `layout.tsx`'s hardcoded `<html>` attributes and become values here. `layout.tsx` renders `data-px-theme={brand.theme}` / `data-px-density={brand.density}`.
- `ThemeName` is the source of truth for which theme blocks must exist in `theme.css`. Adding a theme = extend this union + add one block.

### 2. `app/theme.css` — every design token, organized per-theme

Split `globals.css` into *look* vs *plumbing/utilities*:

- **`app/theme.css`** — the look only. One clearly-labeled block per theme, plus density modifiers:
  ```css
  /* ===== Theme: warm-light (default) ===== */
  :root,
  [data-px-theme="warm-light"] {
    /* --px-* semantic tokens (bg / surface / fg / accent / semantic) */
    /* shadcn semantic mapping (--background, --primary, … → var(--px-*)) */
    /* Tailwind named-palette ramps: --c-zinc-50 … --c-purple-700, etc. */
  }
  /* ===== density ===== */
  [data-px-density="compact"]     { … }
  [data-px-density="comfortable"] { … }
  [data-px-density="spacious"]    { … }

  /* To add a theme: copy the warm-light block, rename the selector to
     [data-px-theme="<name>"], change the values, add "<name>" to
     ThemeName in lib/brand.ts. */
  ```
- **`app/globals.css`** — Tailwind plumbing + components:
  - `@import "tailwindcss";`
  - `@import "./theme.css";`
  - the `@theme inline { … }` mapping block (exposes tokens to utilities)
  - the `.px-btn` / `.px-input` / `.px-badge` / … utility classes (unchanged)
  - the `@layer base` block (unchanged)

**The structural fix that makes the promise real:** the Tailwind named-palette overrides become *references*, resolved per theme. In `globals.css`'s `@theme inline`:
```css
@theme inline {
  --color-zinc-50: var(--c-zinc-50);   /* was: #F6F2EC literal */
  --color-red-500: var(--c-red-500);
  /* …all zinc/neutral/stone/slate/gray/red/rose/green/emerald/teal/
       blue/sky/indigo/amber/yellow/orange/purple/violet/fuchsia ramps… */
  --color-white:   var(--c-white);
}
```
and each `--c-*` is defined inside the per-theme block in `theme.css`. Result: flipping `brand.theme` recolors the **entire** app — semantic tokens *and* legacy named-palette utilities.

- The `var()`-inside-`@theme inline` pattern is already proven in the current file (`--color-px-bg: var(--px-bg)`), so this is mechanical, not novel.
- `@import "./theme.css"` is processed by the Tailwind v4 PostCSS plugin, so `@theme inline` can resolve variables defined in the imported file. (Verify on first build; if `@theme` resolution requires same-file co-location, keep the `@theme inline` block in `globals.css` — which this design already does — and only the `--c-*` / `--px-*` *value* definitions move to `theme.css`.)
- Warm-light values are copied **verbatim** from today's `globals.css`. No visual change in this phase.

### 3. `components/px/BrandLogo.tsx` — `<BrandLogo>` + `<BrandMark>`

Two small client-safe components reading from `brand.ts`, exported from the `px/` barrel (`components/px/index.ts`), per the component-placement rule the user approved:

- `<BrandLogo height={number} className?>` — the wordmark (login, expanded sidebar). Renders `brand.logo.wordmark`, `alt={brand.name}`, displayed at the given height with aspect preserved.
- `<BrandMark size={number} className?>` — the square mark (collapsed sidebar, favicons-in-DOM, tight spots). Renders `brand.logo.mark`, `alt={brand.name}`.

Rendered via `next/image` (lint-clean vs raw `<img>`). **Per `AGENTS.md`, verify the Next 16 `next/image` API in `node_modules/next/dist/docs/` before writing** — the import path / prop contract may differ from training data.

---

## Asset pipeline (one-time, scripted + visually verified)

Source: `assets/BinQle_Logo_3840x2160_Color-Transparent.png` (3840×2160 RGBA, transparent).

1. **Wordmark** → `frontend/app/public/brand/binqle-wordmark.png`
   - Trim transparent padding (so intrinsic aspect is tight), downscale to a web-appropriate width (target ~960px wide, preserving aspect + alpha).
   - Record the trimmed intrinsic `width`/`height` into `brand.ts` `logo.wordmark`.
2. **Mark** → `frontend/app/public/brand/binqle-mark.png`
   - Isolate the gradient **"Q"** glyph: with PIL, take the alpha bounding box, then detect "colorful" pixels (saturation / channel-spread above a threshold — the navy ink is near-grayscale, the Q ring is cyan→purple) to locate the ring's bounding box; crop a **square** centered on it with small transparent padding; resize to 256×256.
   - **Visually verify** the crop (read the output PNG); adjust crop coordinates manually if the heuristic clips or includes the "B" edge bleed. This is the one step expected to need a human-in-the-loop tweak.
3. **Favicon** → regenerate `frontend/app/app/favicon.ico` from the 256px mark (ImageMagick multi-size `.ico`). Keep the existing `favicon.ico` filename convention (zero Next config risk).
4. **Delete** the dead `frontend/app/public/projectx-logo.svg`.

A throwaway script (e.g. `scripts/derive-brand-assets.py`, not committed unless useful) performs steps 1–3; output PNGs/ICO are committed.

---

## Wire-up — replace scattered hardcoding

| File | Change |
|---|---|
| `app/layout.tsx` | `metadata.title = brand.name`; `metadata.description = brand.tagline`; `data-px-theme={brand.theme}`; `data-px-density={brand.density}`. |
| `components/dashboard/AppShell.tsx` | Replace the inline play-SVG box + `"ProjectX"` text with `<BrandMark>` (collapsed) / `<BrandLogo>` (expanded). |
| `app/(auth)/login/page.tsx` | Replace the inline play-SVG + `"ProjectX"` `<h1>` with `<BrandLogo>` + `{brand.loginSubtitle}`. |
| `app/suspended/page.tsx` | Copy uses `{brand.name}`. |
| `app/(dashboard)/settings/team/page.tsx` | "lose access to ProjectX" → `${brand.shortName}`. |
| `app/(dashboard)/settings/integrations/page.tsx` | "so ProjectX can import" → `{brand.shortName}`. |
| `app/(dashboard)/settings/integrations/connect/page.tsx` | Same. |
| `app/(dashboard)/settings/integrations/[connectionId]/page.tsx` | 2 prose strings → `{brand.shortName}`. |
| `components/settings/integrations/JobStatusFilterDialog.tsx` | "should ProjectX import?" → `{brand.shortName}`. |
| `components/settings/integrations/CeipalConnectionForm.tsx` | "ProjectX only fetches…" → `{brand.shortName}`. |
| `app/(dashboard)/settings/org-units/[unitId]/Sidebar.tsx` | "contact ProjectX support" → `{brand.shortName}`. |

**Do NOT touch** `lib/api/ats.ts` `projectx_stage_id` (×2) — that is a backend API field name (wire contract), not a brand string.

---

## Testing

Per the repo's composition-test convention (Vitest + Testing Library + jsdom):

- `tests/components/BrandLogo.test.tsx` — `<BrandLogo>` and `<BrandMark>` render an image with `alt = brand.name` and the configured `src`.
- A lightweight assertion that the sidebar renders the brand (extend or add an AppShell composition test): the brand wordmark/mark image is present (not the old literal "ProjectX" text).
- `npm run lint`, `npm run type-check`, `npm run test`, `npm run build` must all pass (the `satisfies BrandConfig` check guards the config shape).

---

## Documentation updates (part of this effort)

- **`frontend/app/CLAUDE.md`** —
  - "Code shared by duplication with `frontend/session`" table: replace the `public/projectx-logo.svg` row; record the **deliberate divergence** (recruiter app rebranded to BinQle + theme split; session app unchanged, later phase) with a one-line rationale, per the sync rule.
  - Directory Structure: add `lib/brand.ts`, `app/theme.css`, `components/px/BrandLogo.tsx`, `public/brand/`; correct the stale `layout.tsx` note ("Geist fonts, zinc-50 bg" → Inter/Fraunces/JetBrains; theme/density from `brand.ts`).
  - Tailwind Standards / Component Library: document `brand.ts` as the name/logo/active-look source, `theme.css` as the per-theme token source + how to add a theme, and `<BrandLogo>`/`<BrandMark>`.
- **Root `CLAUDE.md`** — the user will review for any "ProjectX" naming that should reflect the rebrand once the platform name is finalized (the root doc names the product throughout; out of scope to mass-rename until the name is locked, but flag it).
- Any other stale references surfaced during implementation are corrected in the same pass.

---

## Out of scope (explicitly deferred)

- Designing new color palettes / a new "design language" (the user's stated next phase). This spec only makes the look swappable and keeps warm-light identical.
- Rebranding `frontend/app/`'s deeper copy, marketing strings, email templates, or the backend.
- Rebranding `frontend/session/` (candidate surface) — separate phase.
- Mass-renaming "ProjectX" across `docs/`, root `CLAUDE.md` prose, READMEs — pending a finalized platform name.
- Adding OAuth/SSO, dark mode as a *designed* theme, or any non-branding UI change.

---

## Verification (definition of done)

1. `brand.ts` is the only place the name/logo/tagline/active-theme are set; grep for visible "ProjectX" in `frontend/app` (excluding `projectx_stage_id`) returns zero.
2. Changing `brand.theme` (once a second theme exists) — or temporarily swapping the `--c-*`/`--px-*` values in the warm-light block — visibly recolors the whole app, including raw `bg-zinc-*`/`text-red-*` utilities. Validated by a manual dev-server check (`curl`/browser) per the user's manual-testing preference.
3. The BinQle wordmark renders on login + expanded sidebar; the derived mark renders in the collapsed sidebar + favicon.
4. `lint` + `type-check` + `test` + `build` green.
5. `frontend/app/CLAUDE.md` reflects the new structure and the session divergence.
