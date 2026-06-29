# Mobile-optimized `/recordings` video playback

**Date:** 2026-06-29
**Status:** Approved (design) — pending implementation plan
**Scope:** `frontend/session` only (the public `/recordings/<token>` page). The recruiter app's authenticated report viewer is a separate component copy and is **out of scope**.
**Related:**
- `docs/superpowers/specs/2026-06-29-public-recordings-share-to-session-design.md` (the move that put these theaters in the session app)

---

## Problem

The video-playback "theaters" on the public `/recordings/<token>` page were ported verbatim from the desktop recruiter app. They have **zero viewport media queries** (only `prefers-reduced-motion`) and are pure fixed-pixel desktop layouts. On a phone:

- `ThisMomentPanel` (fixed **312px** left) + `QuestionRail` (fixed **210px** right) need ~550px and overflow a 390px viewport — panels overlap the video and clip off-screen.
- The top bar crushes brand + candidate name + **5 score gauges** + integrity chip + verdict + close into one row (~460px of content in ~376px).
- The control-bar scrubber collapses to ~124px → coarse, imprecise touch seeking.
- Tap targets are 24–36px (play 36, mute/fullscreen 28, close 24) — below the 44px touch minimum.
- The proctoring flag detail card is **hover-only** (no touch path); iOS fullscreen silently no-ops; nothing handles landscape.

External recruiters (and anyone with the shared PDF link) increasingly open this on phones.

## Goal

Make `/recordings` video playback work well on phones in **both portrait and landscape**, without changing the desktop experience.

## Non-goals

- No change to desktop (`>640px`) layout or to the recruiter app's copy.
- No new runtime dependencies; the session app stays free of `@supabase/*`.
- No switch to the browser's native `<video controls>` (we keep the custom bar to preserve proctoring flag ticks + reel chapter markers + branding).
- No backend changes.

---

## Decisions (locked during brainstorming)

1. **Video-first + bottom sheets** — phone shows an immersive video with big touch controls; the analytical panels (question list, "this moment" detail, score gauges) move into a tap-to-open sheet. Nothing is lost.
2. **Letterbox** — `object-fit: contain` on mobile so the full 16:9 frame shows (no cropping) in both orientations.
3. **Both orientations are first-class** — explicit portrait and landscape handling.
4. Breakpoint at **640px**; **volume slider hidden** on mobile (mute retained).

---

## Breakpoints (single source of truth)

Added to `theater.css` (and mirrored via Tailwind responsive/`max-*` classes where components use Tailwind):

| Name | Query | Used for |
|---|---|---|
| Compact (phone portrait + small) | `@media (max-width: 640px)` | video-first layout, bottom sheet, two-row controls, compact top bar |
| Landscape-compact (phone landscape) | `@media (orientation: landscape) and (max-height: 480px)` | slim auto-hiding overlays, right-side drawer instead of bottom sheet |
| Touch | `@media (hover: none) and (pointer: coarse)` | ≥44px tap targets, tap-to-toggle flag card (orientation-independent) |

Rotation needs **no JS for layout** — the media queries relayout automatically; the `<video>` is not remounted (keeps playing), and React state (sheet/drawer open, playback position) survives the restyle.

---

## Design

### 1. Video surface — `TheaterStage.tsx` + `theater.css`
- `object-fit: contain` under the Compact + Landscape-compact queries (desktop keeps `cover`).
- Add `playsInline` (+ `webkit-playsinline`) to the `<video>` so iOS plays inline instead of hijacking into native fullscreen on play. **This is the load-bearing iOS fix.**

### 2. Touch-friendly controls — `VideoControls.tsx` + `theater.css`
- **Two-row control bar** under Compact: scrubber on its own full-width row (fixes the 124px collapse), then a button row.
- **≥44px tap targets** under the Touch query: play, mute, speed, fullscreen, and both close buttons (`TheaterTopBar` close, `ReelTheater` close).
- **Hide the volume slider** on mobile; keep the mute toggle.
- **Fullscreen, robust across platforms** (`useVideoController.ts`):
  - If `element.requestFullscreen` exists → standard fullscreen.
  - Else if the `<video>` exposes `webkitEnterFullscreen` (iOS Safari) → call it on the video element.
  - Else → hide the fullscreen button (feature-detected; no dead button).
- **Proctoring flag ticks** (`VideoControls.tsx`): under the Touch query, make ticks **tap-to-toggle** the detail card (pointer hover doesn't exist on touch). Clamp the card within the viewport (it currently positions at `clientX` and can render off-screen). Tapping elsewhere / scrubbing dismisses it.

### 3. ReviewTheater panels → mobile sheet/drawer — new `TheaterMobileSheet.tsx`
- New mobile-only component fed by the existing `useTheaterState` data (questions, active question, scores, verdict, "this moment" detail) — **no new data flow**.
- Under Compact (portrait): the fixed side panels (`ThisMomentPanel`, `QuestionRail`) are `display:none`; a **"Questions & scores" trigger** in the control area opens a **bottom sheet** (height capped ~70vh, internally scrollable) containing, in order: verdict + the 5 score gauges, the question list (tap a question → `seek()` + set active), and the active question's "this moment" detail.
- Under Landscape-compact: the same component renders as a **right-side drawer** (full height, ~min(360px, 80vw) wide, slide-in) so it doesn't consume the scarce vertical space.
- Desktop (`>640px`): the sheet/drawer + trigger are hidden; the existing side panels render exactly as today.
- Open-state is React state on `ReviewTheater`, so it persists across rotation (bottom sheet ⇄ right drawer is purely a CSS restyle of the same mounted component).

### 4. Top bar — `TheaterTopBar.tsx`
- Under Compact: collapse to brand mark + candidate name (truncated) + verdict pill + close (≥44px). The 5 inline gauges are removed from the bar (they live in the sheet per §3).
- Under Landscape-compact: the bar becomes a slim, auto-hiding overlay (shares the controls' visibility timer) so the video uses the full short height.

### 5. ReelTheater — `ReelTheater.tsx`
- Inherits §1–2 (contain, `playsInline`, two-row touch controls, fullscreen handling). **Chapter markers stay** on the scrubber. No sheet/drawer (the reel has no side panels).

### 6. Toggle — `PublicRecordingsView.tsx`
- The reel/full-session toggle pill gets ≥44px touch height under the Touch query and is positioned so it does not collide with the compact top bar (and clears the landscape overlay).

---

## Testing

- **Vitest:** existing `recordings-route` + `recordings-layout` tests stay green. Add a test that the mobile sheet **trigger** renders and toggles the sheet open/closed, and that the trigger is not rendered on desktop width (JSDOM: assert the trigger element + the side panels' presence via a width/`matchMedia` mock, or assert both the desktop panels and the mobile trigger exist in the DOM and rely on CSS to show/hide — pick the approach that fits the component's render logic). At minimum: the sheet content is reachable (renders the question list + scores) when opened.
- **Layout/visual (manual, per project "verify served frontend"):** `curl -I`/load the running dev server at `/recordings/<token>` and verify in a real phone (portrait + landscape) — no horizontal overflow, video letterboxed, controls ≥44px, sheet opens (bottom in portrait, side in landscape), flag detail opens on tap, fullscreen works or button hidden, rotation relayouts without losing playback.
- **Constraints:** no new deps; session stays `@supabase`-free; desktop (`>640px`) renders byte-for-byte as before (changes are additive media queries + a mobile-only component gated off on desktop).

## Accepted scope notes

- Only the `frontend/session` theater copy changes; the recruiter app's copy keeps its desktop-only layout (accepted two-app drift).
- The custom control bar is retained (not swapped for native `<video controls>`) to preserve flag ticks, chapter markers, and branding.
