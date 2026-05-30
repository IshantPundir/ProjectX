# Vision Proctoring — DPIA + Bias-Review Note

**Date:** 2026-05-30
**Status:** Pre-production action item (spec §10, §16.8 — must not be dropped before GA)
**Scope:** Server-plane vision proctoring — post-session MobileGaze ONNX analysis pipeline
**Spec reference:** `docs/superpowers/specs/2026-05-29-vision-proctoring-design.md` §10 + §16

---

## Purpose of this document

This note fulfils the pre-production compliance obligations called out in the
vision-proctoring spec (§10 "Pre-production action items" and §16.8). It is
deliberately short — a lightweight DPIA stub and bias-review record, not a full
GDPR DPIA submission — sufficient for internal audit and for satisfying the
"documented lawful basis + DPIA note under docs/security/" requirement from
§10. If a formal GDPR supervisory-authority notification is required for a
specific client, this document is the starting point for the Article 35
submission.

---

## 1. Data processed

### 1a. R2 recording (already-consented biometric video)

The post-session gaze pipeline inputs the MP4 recording already held in the
Cloudflare R2 recording bucket. That recording was captured under the
timestamped consent logged before `/start` (AIVIA-compliant). It is:

- per-session, tenant-prefixed, bucket-private, short-lived-presigned-URL
  access only (no public read) — same controls documented in
  `docs/security/threat-model.md` §Session recording.
- classified as **special-category data** (GDPR Art. 9) and as potentially
  biometric under US BIPA because it contains the candidate's face (geometry
  analysis is one step downstream).

The recording itself is not a new processing act — it was created and is
governed by the existing session-recording consent and DPA. The vision
pipeline is a new downstream processing purpose over that recording.

### 1b. Derived features stored (features only — never raw frames)

The vision worker (`nexus-vision-worker`) downloads the recording, samples
frames via `ffmpeg` at ~5 fps, and runs the gaze + face detector. It then
**discards all frames and crops** and persists only derived features to the
`session_proctoring_analysis` table (spec §16.6, D6):

| Field | Description | Personal-data category |
|---|---|---|
| `risk_band` | Coarse 3-tier label: `low` / `medium` / `high` / `insufficient_data` | Derived; no biometric template |
| `detector_summary` | Aggregate fractions: `off_screen_pct`, `down_glance_count`, `reading_sweep_intervals`, `max_faces`, `multi_face_intervals` | Derived aggregate |
| `gaze_heatmap` | 5×5 yaw×pitch occupancy grid (deviation from per-session baseline) + off-screen-% timeline | Derived relative measurement |
| `flagged_intervals` | `[{start_ms, end_ms, kind, confidence}]` — timestamps of notable gaze events | Derived temporal metadata |
| `gaze_signal_quality` | `good` / `glasses-degraded` / `low-light` / `unscorable` | Derived quality indicator |
| `unscorable_pct` | Fraction of frames with no scorable face | Derived aggregate |
| `model_versions` | Gaze-model ID + weights hash + pipeline version (for EEOC audit trail) | Technical metadata |

**What is NOT stored:** raw video frames, face crops, face embeddings, iris
templates, biometric feature vectors, or any other form that could be used for
identity matching. The only personal link to the candidate is the `session_id`
foreign key.

---

## 2. Lawful basis and consent

### GDPR

Lawful basis: **explicit consent** (Art. 6(1)(a) + Art. 9(2)(a)) for
special-category data (facial geometry / eye analysis). The consent is:

- **Pre-session, timestamped, and logged** to the session record before any
  recording begins (AIVIA compliance, already required by the existing session
  flow).
- **Versioned.** The consent string must be extended before enabling vision
  proctoring to disclose camera-based automated monitoring including
  **facial-geometry and eye-direction analysis**. The version is stored with
  the session so the consent text that governed a given session can be
  reconstructed for audit.
- **Granular for the vision plane.** The `proctoring_vision_enabled` tenant
  setting gates the server-plane analysis. A tenant that has not opted in does
  not trigger the analysis actor regardless of the candidate's general consent.

### BIPA (Illinois Biometric Information Privacy Act)

Face geometry extraction during analysis can constitute a biometric identifier
under BIPA even if no identity-matching is performed. BIPA requires:

1. **Written policy** — to be published before collection begins.
2. **Informed consent** with specific disclosure of the fact that facial
   geometry is being extracted, the specific purpose, and the retention
   schedule.
3. **No-sale** commitment (never sell or profit from biometric data).
4. **Written retention/destruction schedule** — features are tied to the
   session record and deleted when the session record is deleted (see §4 below).

The consent-gate extension required for the vision plane (§10 D8) must include
this BIPA disclosure for any candidate whose session is covered by Illinois law.
Declining blocks session start when vision proctoring is enabled (D8).

### AIVIA (Illinois AI Video Interview Act)

An AI-monitoring disclosure line must be added to the pre-session disclosure.
This is already triggered by the general AI-led interview consent; the vision
plane requires a specific line covering automated facial/gaze analysis during
the interview.

---

## 3. Purpose limitation

The vision analysis result is **evidence for human recruiter review only**.

- The `risk_band` label and flagged-interval list are displayed in the
  "Proctoring & Integrity" panel on the recruiter report page.
- The UI labels everything "for review, not a decision" (spec §16.5).
- **The vision analysis result never auto-rejects or auto-advances a
  candidate.** The borderline-candidate invariant (human sign-off required on
  all final hiring decisions) applies regardless of the `risk_band` value.
- The feature outputs must not be used for identity recognition, profiling
  outside the hiring decision, or any secondary purpose.

---

## 4. Retention and deletion

- The `session_proctoring_analysis` row is tied to `session_id` via a
  foreign-key `ON DELETE CASCADE` constraint. It is deleted automatically when
  the parent session record is deleted.
- Candidate data deletion workflows (GDPR/CCPA deletion-on-request) must
  include the `sessions` table deletion path, which will cascade to
  `session_proctoring_analysis`.
- The R2 recording itself is governed by the existing recording-bucket deletion
  policy. The gaze features have no longer retention than the recording.
- No separate retention clock for `session_proctoring_analysis`; its lifecycle
  is subordinate to the session record.

---

## 5. Bias-review obligation

### Known performance gaps

Appearance-based gaze models have documented demographic performance
disparities:

- **Skin tone.** Darker skin tones produce lower face-detector confidence
  scores and higher gaze-angle estimation error due to reduced contrast in the
  Lambertian-reflectance components that gaze models rely on.
- **Eyewear (glasses, sunglasses).** Glasses produce corneal reflections and
  frame occlusion that degrade both face alignment and gaze estimation quality.
  This is the most prevalent degradation pathway for a typical Indian candidate
  population (commodity laptop + glasses).
- **Head pose extremes.** Large yaw/pitch (candidate looking far to the side)
  causes face-crop quality drops that cascade to higher gaze-angle variance,
  which can trigger false `off_screen_sustained` intervals.
- **Illumination.** Low ambient light (side-lit, backlit, night) degrades
  detector confidence below the scorable threshold; high UV/fluorescent glare
  can produce similar effects.

### Current mitigations

- **`insufficient_data` band.** When `unscorable_pct` exceeds the configured
  threshold (default 0.6), the band is set to `insufficient_data` — the
  reviewer sees no confident accusation on frames that were demonstrably
  unseeable.
- **`gaze_signal_quality` field.** Surfaces the quality tier
  (`good` / `glasses-degraded` / `low-light` / `unscorable`) directly on the
  recruiter panel. Reviewers are expected to weight the band lower when quality
  is degraded.
- **`model_versions` audit trail.** The gaze model ID, weights hash, and
  pipeline version are persisted per row for EEOC retrospective audit if a
  systematic bias complaint arises.
- **Human-review-only (D1).** No automated adverse action is ever taken on the
  basis of the vision analysis (§3 above). This is the primary bias-impact
  mitigation — a human evaluates the flag in context before any hiring decision.
- **Confidence thresholds on intervals.** Each `flagged_interval` carries a
  `confidence` field; low-confidence flags are displayed distinctly in the UI.

### Documented review obligation

The band thresholds (off-screen-%, down-glance-count, reading-sweep triggers)
are **tuned on synthetic angle streams and preliminary self-recorded sessions**,
not on a demographically representative dataset. Before GA or before enabling
the vision plane for any production tenant, the following must be completed:

1. **Collect a demographically diverse calibration set.** At minimum: varied
   skin tones (Fitzpatrick I–VI), with and without glasses, varied ambient
   lighting conditions. The target population (Indian candidates, commodity
   laptops) must be represented.
2. **Measure false-positive rates per subgroup** on the above set. The `medium`
   and `high` band thresholds should produce false-positive rates that are not
   statistically distinguishable across subgroups at the planned session volume.
3. **Adjust thresholds or widen `insufficient_data` criteria** based on the
   measured subgroup gaps, and document the adjustment rationale here.
4. **Sign off the bias review** in this document before production traffic is
   routed through the analysis actor. The sign-off should include the test-set
   size, subgroup breakdown, and the threshold delta applied (if any).

**This bias-review obligation is OPEN.** It is not satisfied by shipping with
the current synthetic-tuned thresholds. The `proctoring_vision_enabled` flag
defaults to OFF/opt-in (§16.8) precisely so production sessions are not
analysed before this review is complete.

---

## 6. Open GA blocker — NC Gaze360 weights

The v1 gaze estimator (`gaze/mobilegaze.py`) wraps the MobileGaze
`resnet34_gaze.onnx` weights. These weights were trained on the **Gaze360
dataset**, which is licensed for **research and non-commercial use only**.

**This makes the current weights legally unsafe to ship to any paying tenant.**

The `GazeEstimator` seam in `gaze/base.py` exists precisely to isolate this
swap: replacing the weights (or the estimator implementation) is an environment
variable change plus a different ONNX file, with no downstream code change.

Paths to a commercially-clean replacement:

1. **Retrain the MobileGaze (MIT-licensed) architecture** on a synthetically
   generated dataset (e.g., UnityEyes or NVGaze, both synthetic/permissive)
   or on Gaze360-*excluded* GazeCapture subsets. This preserves appearance-CNN
   accuracy.
2. **MediaPipe Face Landmarker** (Apache-2.0, no dataset taint) exposes
   `eyeLook{Up,Down,In,Out}` blendshapes + head pose. Lower accuracy ceiling
   than an appearance CNN, but zero licensing risk. The wrapper would implement
   the `GazeEstimator` protocol; downstream pipeline is unchanged.

**Do not close this item by shipping the NC weights to a paying tenant, even
under an enterprise contract that includes broader software-use terms — the
Gaze360 dataset license is not sub-licensable by ProjectX.**

Tracking: this blocker must be resolved and documented here before any commit
that flips `proctoring_vision_enabled` to ON-by-default or enables it for a
production tenant.

---

## 7. Sub-processors

The server-plane vision analysis introduces **no new external sub-processors**.
All computation runs in-house (the `nexus-vision-worker` container in the
same infra as the Nexus API). The only external service in the data path is
Cloudflare R2, which is already a sub-processor (existing recording pipeline)
and whose DPA and data-path are documented in the threat model
(`docs/security/threat-model.md` §Session recording).

---

## Open items (pre-GA checklist)

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Extend consent string to disclose facial-geometry/eye-direction analysis; version the string | Engineering | OPEN |
| 2 | BIPA retention/destruction written policy published | Legal/Product | OPEN |
| 3 | Bias-review (§5) — calibration set + threshold sign-off | Engineering + Product | OPEN |
| 4 | Replace NC Gaze360 weights with commercially-clean weights before enabling for any paying tenant (§6) | Engineering | OPEN |
| 5 | Flip `proctoring_vision_enabled` default → OFF/opt-in before any production deploy | Engineering | OPEN (spec §16.8 / §10 D7) |
| 6 | Add vision proctoring to ATS/DPA sub-processor register (if extended storage or new external service added) | Legal/Product | OPEN |
