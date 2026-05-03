/**
 * Measure the room's noise floor over a 2-second window via the Web
 * Audio API. Returns the result as dBFS (always negative; closer to 0
 * = louder). Never throws — returns null on any failure so the caller
 * can degrade silently rather than block the candidate.
 *
 * Extracted into its own module so tests can stub it cleanly via
 * vi.mock('@/app/interview/[token]/sampleNoiseFloorDbfs') without
 * fighting against AudioContext / requestAnimationFrame / performance.now
 * timing issues in jsdom.
 */

const SAMPLE_DURATION_MS = 2000

export async function sampleNoiseFloorDbfs(
  stream: MediaStream,
): Promise<number | null> {
  // Some older Safari builds need the prefixed constructor.
  const Ctor =
    typeof window !== 'undefined'
      ? window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext
      : null
  if (!Ctor) return null

  const ctx = new Ctor()
  try {
    const source = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 2048
    source.connect(analyser)
    // NOT connected to ctx.destination — we don't want to play it back.

    const buf = new Float32Array(analyser.fftSize)
    let sumSquares = 0
    let sampleCount = 0

    const startedAt = performance.now()
    while (performance.now() - startedAt < SAMPLE_DURATION_MS) {
      analyser.getFloatTimeDomainData(buf)
      for (let i = 0; i < buf.length; i++) {
        sumSquares += buf[i] * buf[i]
        sampleCount++
      }
      await new Promise<void>((r) => requestAnimationFrame(() => r()))
    }

    if (sampleCount === 0) return null
    const rms = Math.sqrt(sumSquares / sampleCount)
    // Floor RMS at 1e-9 so log10 doesn't return -Infinity on dead silence.
    return 20 * Math.log10(Math.max(rms, 1e-9))
  } catch {
    return null
  } finally {
    try {
      await ctx.close()
    } catch {
      // already-closed contexts are not an error worth surfacing.
    }
  }
}
