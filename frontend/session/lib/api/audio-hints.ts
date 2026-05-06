import type { AudioProcessingHints } from './candidate-session'

/**
 * Convert server-provided audio processing hints into the
 * AudioCaptureOptions / getUserMedia audio constraints object
 * the LiveKit React SDK / browser API expects (camelCase).
 *
 * The server is the source of truth: when server-side enhanced NC
 * is on (Cloud mode), `noise_suppression` is false so the ML model
 * sees raw audio. EC and AGC stay true in both modes (load-bearing
 * for full-duplex; ai-coustics is not an EC).
 */
export function toAudioCaptureOptions(hints: AudioProcessingHints): {
  noiseSuppression: boolean
  echoCancellation: boolean
  autoGainControl: boolean
} {
  return {
    noiseSuppression: hints.noise_suppression,
    echoCancellation: hints.echo_cancellation,
    autoGainControl: hints.auto_gain_control,
  }
}
