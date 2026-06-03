# Post-mortem: Vision proctoring worker CPU exhaustion

- **Date:** 2026-06-01
- **Severity:** SEV3 (single-feature degradation; dev/POC environment — no tenant impact)
- **Status:** Resolved (code mitigations merged; feature remains a gated POC)
- **Author:** Ishant
- **Blameless:** Yes — this is a learning record, not a fault record.

> Per the root `CLAUDE.md` Incident Response standard, SEV1/SEV2 require a written
> blameless post-mortem within 5 business days. This was a SEV3 caught in development;
> the post-mortem is recorded here because it produced first-class action items and to
> seed `docs/incidents/`.

---

## Summary

The server-plane vision proctoring worker (`app/modules/vision/`, the dedicated
`nexus-vision-worker` container) pegged **~23 CPU cores** while analyzing a single
~14-minute interview recording. The worker was stopped and the in-progress analysis
row neutralized to recover the machine. No tenant data was exposed and no candidate
or recruiter flow was affected — vision proctoring is a gated POC and was not on any
tenant's critical path.

## Impact

- Development machine CPU saturated (~23 cores) by one ONNX gaze-analysis job.
- Risk of runaway re-enqueue compounding the load.
- No production tenants affected. No data loss. No isolation/security impact.

## Root cause

The gaze-analysis pipeline was unbounded along several axes simultaneously:

1. **Frame sampling too dense** — `vision_sample_fps` defaulted to 5.0, producing far
   more frames than needed for coarse head-pose/gaze risk banding.
2. **Unbounded native threading** — ONNX Runtime (and underlying OMP/BLAS) spun up
   per-core threads per inference, and the estimator was being constructed more than
   once per process, multiplying thread pools.
3. **No process-level CPU ceiling** — the worker container had no `cpus` cap, so the
   native thread explosion consumed whatever the host offered.
4. **Re-enqueue amplification** — a stale/running analysis row could be re-enqueued,
   stacking concurrent heavy jobs.

## Contributing factors

- The module is a POC built GPU-first in intent, but was running CPU inference on a
  dev box without GPU passthrough — the worst-case path.
- No load test against a full-length recording before enabling the enqueue path.

## Detection

Manual — observed CPU saturation on the development host during a routine end-to-end
session test.

## Mitigation / resolution

Immediate (operational): stopped `nexus-vision-worker`; neutralized the in-progress
`session_proctoring_analysis` row.

Code mitigations (merged, separate branch off `main`):

- `vision_sample_fps` default **5.0 → 2.0**.
- ONNX Runtime intra-op threads pinned to **1** (`vision_ort_intra_op_threads=1`); OMP
  thread caps; estimator loaded **once per process**.
- **GPU-first** provider order (`CUDAExecutionProvider,CPUExecutionProvider`).
- Hard **`cpus: 4`** backstop on the `nexus-vision-worker` compose service (inline
  comment references this incident).
- **Enqueue guard** (`session/recording.py::_maybe_enqueue_vision` +
  `_vision_analysis_needs_enqueue`) — only enqueues when no row exists or a prior run
  is genuinely stale, killing the re-enqueue loop.
- `vision_max_frames` / `vision_max_frame_width` ceilings.

## Action items

| # | Action | Owner | Status |
|---|---|---|---|
| 1 | Cap frame sampling + native threads; add `cpus` backstop | Ishant | ✅ merged |
| 2 | Kill the re-enqueue amplification (enqueue guard) | Ishant | ✅ merged |
| 3 | GPU-first inference; document that CPU-only is the slow path | Ishant | ✅ merged |
| 4 | Load-test the analyzer against a full-length recording on the target (GPU) hardware before any tenant enablement | Ishant | ⬜ open (GA gate) |
| 5 | Replace non-commercial gaze weights before any production use | Ishant | ⬜ open (GA blocker — see DPIA) |

## Lessons

- Any CPU/GPU-bound worker needs an explicit resource ceiling **and** a native-thread
  cap from the first commit — defaults assume a server, not a shared dev box.
- Re-enqueue paths for long-running jobs must be idempotent and guarded against
  stacking before the work is ever enabled.

## References

- Perf design: `docs/superpowers/specs/2026-06-01-vision-proctoring-perf-design.md`
- Plan: `docs/superpowers/plans/2026-06-01-vision-proctoring-perf.md`
- DPIA: `docs/security/2026-05-30-vision-proctoring-dpia.md`
- Threat model: `docs/security/threat-model.md` → "Vision proctoring — server-plane analysis"
