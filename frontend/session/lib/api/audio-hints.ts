import type { AudioProcessingHints } from './candidate-session'

/**
 * Convert server-provided audio processing hints into the
 * AudioCaptureOptions / getUserMedia audio constraints object
 * the LiveKit React SDK / browser API expects (camelCase).
 *
 * Pure passthrough mapper: renames snake_case server keys to
 * camelCase browser keys. The server is the source of truth —
 * it sets `noise_suppression: true` so the browser does light NS;
 * there is no server-side NC. EC and AGC stay true (load-bearing
 * for full-duplex).
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
