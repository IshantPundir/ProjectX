# Review Theater — Glassmorphic Redesign (Frontend)

**Date:** 2026-05-30
**Surface:** `frontend/app` (recruiter dashboard)
**Scope:** 100% frontend. No backend or API changes. The backend Report Review Theater
plan (`2026-05-30-report-review-theater-backend.md`) is already merged — the API serves
`questions[].asked_at_ms`, `questions[].thumbnail_url`, and `flagged_intervals[].thumbnail_url`
today. This redesign is a visual + interaction overhaul of the existing theater that
consumes that contract.

---

## Goal

Transform the report Review Theater from a light, boxed dialog (1160×760, native video
controls, solid panels beside the video) into an **immersive, dark-glassmorphic playback
theater**: a near-full overlay where the **video is full-bleed** and all UI is **dark
smoked-glass panels floating over it**, with **fully custom video controls** and a
**legible 3-layer session timeline**.

Aesthetic reference: the "int." live-interview mockup (`tmp/original-*.webp`) — glass
language, full-bleed subject, circular controls, thumbnail-card timeline. Adopted
**aesthetically, not literally**: we keep the product's real panels and drop reference-only
elements (call-avatar row, portfolio cards, radar hexagon) that don't map to a playback
review.

---

## Design decisions (locked)

1. **Fidelity:** Aesthetic, not literal. Keep real panels (identity+gauges, This-Moment,
   3-layer timeline). Drop call avatars / portfolio cards / radar.
2. **Glass tone:** Dark smoked glass with light text — legible over any video frame.
3. **Size:** Near-full overlay (~96vw × 94vh) with margin + backdrop blur behind it.
4. **Scores viz:** Keep compact ring gauges (Overall / Technical / Comms) in the top bar.
   No radar panel.

---

## Diagnostic context (why two "bugs" are stale-data, not code)

Verified against the test session `ee1e6683-f878-405c-a53c-48c973f786f4`:

| Symptom | DB reality | Classification |
|---|---|---|
| Integrity timeline looks empty | `status=ready`, `risk=high`, **64 flagged intervals** (42 down_glance + 22 off_screen_sustained) | **Real frontend bug** — rendering doesn't convey the mass. Fix in this redesign. |
| No question-card thumbnails | `session_timeline_thumbnails` has **flag:6, question:0**; transcript has **0 entries with `question_id`** (session predates engine tagging) | **Stale data.** New sessions populate it. Degrade gracefully. |
| Clicking a tab doesn't seek | every question's `asked_at_ms` is **null** (same root cause) → `seekToMs(null)` no-ops | **Stale data.** Wiring is correct; harden the dead-click. |

The redesign must **light up automatically** for a fresh (post-engine-tagging) session with
no further code change, and **degrade intentionally** for legacy sessions like the test one.

---

## Architecture

All work under `frontend/app/components/dashboard/reports/theater/`. The component
decomposition stays close to today's; the layout model and styling change.

### Layout model (the core change)

Today: flex column `[topbar] / [stage | side-panel] / [timeline]` with the video boxed in
the middle cell.

New: a **layered stage**. The video is the full-bleed background of the shell
(`object-cover`, black behind, gradient scrims top+bottom for glass contrast). Every other
surface is an **absolutely-positioned dark-glass panel** layered above it via `z-index`:

```
┌──────────────────────────────────────────────────────────┐
│ ░ top bar: identity · ◐◐◐ gauges · risk · verdict · ✕ ░   │ z=top, full width
│                                                            │
│                      [ VIDEO FILL ]            ┌─────────┐ │
│                                                │ ░ THIS  │ │ z=right, v-centered
│                                                │ MOMENT ░│ │
│                                                └─────────┘ │
│  ⓘ ▶ ──────────●──────────── 02:14 / 04:11  1× 🔊 ⛶  ░    │ z=controls (auto-hide)
│ ░ filmstrip · node track · integrity lane ░               │ z=dock, full width
└──────────────────────────────────────────────────────────┘
```

The control bar and timeline dock stack at the bottom (controls above dock). Top bar,
This-Moment, control bar, and dock are all `theater-glass` (dark variant).

### Components

| Component | Change |
|---|---|
| `ReviewTheater.tsx` | Rewrite layout to layered/absolute model. Video as background layer; panels as overlays. Mouse-idle → auto-hide control bar (and optionally top bar) with reveal-on-move. |
| `TheaterStage.tsx` | Remove `controls`. `object-cover` full-bleed. Add gradient scrims. Keep `videoRef` + `seekApiRef` + `onTimeUpdate`. Expose play state, duration, buffered, volume, rate to the new controls (via state lifted into `useTheaterState` or a small `useVideoController` hook). |
| `VideoControls.tsx` (**new**) | Glass control bar: circular play/pause, draggable scrubber (buffered range + hover-scrub time tooltip), `mm:ss / mm:ss`, volume (mute + hover slider), speed cycle (1×/1.5×/2×), fullscreen. Auto-hide. |
| `TheaterTopBar.tsx` | Restyle for dark glass. Keep identity + 3 ring gauges + risk chip + verdict chip + close. |
| `ThisMomentPanel.tsx` | Restyle for dark glass; float right, vertically centered. Same 3 states (decision / question / flag). |
| `SessionTimeline.tsx` | Restyle dock for dark glass. Same 3-layer composition. |
| `Filmstrip.tsx` | Dark-glass cards. **Thumbnail placeholder**: tone-tinted gradient + Q-number when `thumbnailUrl` is null. **Non-seekable affordance** when `askedAtMs` is null (dimmed/cursor-default, aria notes no timestamp). |
| `NodeTrack.tsx` | Restyle for dark glass. Nodes only render where `askedAtMs != null` (already true). |
| `IntegrityLane.tsx` | **Reworked** (see below). |
| `theater.css` | Rewrite: dark glass variables, near-full overlay sizing, scrim gradients, control-bar + scrubber styles, integrity-lane styles, auto-hide transitions. |
| `timeline-model.ts` | Add a second density series so down-glance and off-screen can be split into sub-lanes (or compute per-kind buckets). Pure functions; unit-testable. |
| `useTheaterState.ts` | Extend to own video transport state if a separate hook isn't cleaner. Keep selection/seek API. |

### Custom video controls (replaces `<video controls>`)

- **Play/pause** circular accent button.
- **Scrubber:** draggable; shows buffered range; hover anywhere shows a scrub-time tooltip;
  click/drag sets `video.currentTime`. This is **raw video position**, distinct from the
  semantic NodeTrack/IntegrityLane below.
- **Time readout** `mm:ss / mm:ss`.
- **Volume:** mute toggle + slider revealed on hover.
- **Speed:** cycle 1× → 1.5× → 2×.
- **Fullscreen:** requestFullscreen on the shell.
- **Auto-hide:** controls fade after ~2.5s mouse-idle, reveal on mousemove.
- **Keyboard:** Space = play/pause, ←/→ = ±5s, `f` = fullscreen, `m` = mute.

### Integrity lane (the real bug fix)

Today: 48 faint pink density slivers + 6 hairline markers → 64 intervals visually vanish on
light bg. New design:

- **Taller band**, **two stacked sub-lanes** color-coded by kind (down_glance vs
  off_screen_sustained) so 42 + 22 read as actual mass.
- **Gamma-curved density mapping** (bright even for a single hit) tuned for the dark glass.
- **All flag ticks clickable** (not just top-6). Top-N by severity show a **thumbnail on
  hover** (the 6 `flag` thumbnails that exist).
- **Bold legible caption** on dark glass: `⚠ HIGH RISK · 36% off-screen · 42 down-glances`
  (pulled from `detector_summary` / counts already available).

### Graceful degradation (legacy sessions)

- Null `thumbnailUrl` → tone-tinted gradient placeholder + Q-number (never a broken image).
- Null `askedAtMs` → card visibly non-seekable; no NodeTrack node; no dead-click.
- Proctoring `status != ready` → existing polling continues; lane shows a processing state
  rather than empty.

---

## Data flow (unchanged contract)

- `useSessionRecording(sessionId)` → `{ signed_url, duration_seconds, offset_ms, transcript }`.
  Video src + transport. `offset_ms` maps engine-ms ↔ video-seconds for seeks.
- `useSessionProctoring(sessionId)` → `{ status, risk_band, detector_summary,
  flagged_intervals[], ... }`. Each top flag may carry `thumbnail_url`.
- `report.questions[]` → `{ seq, question_id, title, status_badge, candidate_quote,
  our_read, asked_at_ms, thumbnail_url }`. Drives filmstrip + nodes + This-Moment.

Seek conversion (existing, kept): `video.currentTime = (ms + offset_ms) / 1000`;
playhead-derived ms = `currentTime*1000 - offset_ms`.

---

## Testing

- **Pure model tests** (`timeline-model.ts`): per-kind density buckets, node positions,
  active-item derivation, null-`askedAtMs` exclusion. Vitest, no DOM.
- **Component tests** where they pay off: Filmstrip placeholder + non-seekable rendering
  (null thumbnail / null askedAtMs); IntegrityLane renders N ticks for N intervals;
  VideoControls play/pause/scrub callbacks fire against a mocked video element.
- **Manual verification** (per repo norm for visual work): run `npm run dev` (port 3000),
  open the test report URL, confirm: dark-glass full-bleed layout; custom controls
  play/scrub/volume/speed/fullscreen; integrity lane shows the 64 intervals legibly with
  hover thumbnails on the 6 flag thumbs; question cards show placeholders + non-seekable
  state (stale session). Then verify a fresh session lights up thumbnails + seek with no
  code change.
- `npm run build` + `npm run lint` clean.

---

## Out of scope

- Any backend / API / migration change (all already merged).
- Radar / hexagon scores panel.
- Literal reproduction of reference-only elements (call avatars, portfolio cards).
- Rebranding (handled separately).
