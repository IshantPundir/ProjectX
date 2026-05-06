# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

# One purpose

This app exists for one user (the candidate) and one task (joining a single live interview). Do not add recruiter features, admin features, account management, or anything that requires Supabase auth. If a feature touches anything other than the candidate's own session, it belongs in `frontend/app/` or `frontend/admin/`, not here.

# Token discipline

The candidate JWT is in the URL path. Never log it, never store it, never send it to any third party. When Sentry is wired (future PR), its `beforeSend` MUST scrub `/interview/[^/]+` paths.

# Audio constraints — DO NOT hard-code

DO NOT hard-code `noiseSuppression: true` (or `false`) in `getUserMedia` or `AudioCaptureOptions`. The server decides — read `audio_processing_hints` from the `/start` response.

Use `lib/api/audio-hints.ts::toAudioCaptureOptions` to convert the server payload to LiveKit's camelCase `AudioCaptureOptions`. Pass the result straight in — no local overrides.

**Why:** In LK Cloud mode (server NC on) `noise_suppression` is `false` so ai-coustics QUAIL_L sees raw audio. `echo_cancellation` and `auto_gain_control` are `true` in both modes — never turn them off.

If `audio_processing_hints` is missing from a `/start` response, fall back to `{ noiseSuppression: true, echoCancellation: true, autoGainControl: true }` and log a warning — do NOT silently omit all constraints.
