import { describe, expect, it } from 'vitest'
import type { AudioProcessingHints } from '@/lib/api/candidate-session'
import { toAudioCaptureOptions } from '@/lib/api/audio-hints'

describe('toAudioCaptureOptions', () => {
  it('maps server hints with NS off (legacy/explicit input)', () => {
    const hints: AudioProcessingHints = {
      noise_suppression: false,
      echo_cancellation: true,
      auto_gain_control: true,
    }
    expect(toAudioCaptureOptions(hints)).toEqual({
      noiseSuppression: false,
      echoCancellation: true,
      autoGainControl: true,
    })
  })

  it('self-hosted mode (server NC off) leaves all browser filters on', () => {
    const hints: AudioProcessingHints = {
      noise_suppression: true,
      echo_cancellation: true,
      auto_gain_control: true,
    }
    expect(toAudioCaptureOptions(hints)).toEqual({
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
    })
  })

  it('renames snake_case server keys to camelCase browser keys', () => {
    const hints: AudioProcessingHints = {
      noise_suppression: false,
      echo_cancellation: false,
      auto_gain_control: false,
    }
    const result = toAudioCaptureOptions(hints)
    // verify keys are exactly the camelCase shape, no leftover snake_case
    expect(Object.keys(result).sort()).toEqual(
      ['autoGainControl', 'echoCancellation', 'noiseSuppression'],
    )
  })
})
