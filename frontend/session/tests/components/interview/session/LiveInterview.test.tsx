import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => ({ room: { state: 'connected', localParticipant: {
    setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
    setCameraEnabled: vi.fn().mockResolvedValue(undefined),
    getTrackPublication: () => undefined,
  } } }),
  useSessionMessages: () => ({ messages: [{ id: '1', timestamp: 1, from: { isLocal: false }, message: 'Tell me about a project.' }] }),
  useVoiceAssistant: () => ({ state: 'speaking', audioTrack: undefined }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
  useMultibandTrackVolume: () => [0],
}))
vi.mock('@/components/interview/app/hooks/use-stage-progress', () => ({
  useStageProgress: () => ({ currentQuestion: 1, totalQuestions: 8, timeRemainingSeconds: 750 }),
}))
vi.mock('@/components/agents-ui/agent-audio-visualizer-aura', () => ({
  AgentAudioVisualizerAura: () => <div role="img" aria-label="AI interviewer" data-testid="stock-aura" />,
}))

import { LiveInterview } from '@/components/interview/session/LiveInterview'

describe('LiveInterview', () => {
  it('renders the aura hero, progress, caption, panel pill and End control', () => {
    render(<LiveInterview companyName="Acme" jobTitle="Senior Engineer" onEnd={vi.fn()} />)
    expect(screen.getByRole('img', { name: /ai interviewer/i })).toBeInTheDocument()
    expect(screen.getByText(/Question 2 of 8/)).toBeInTheDocument()
    expect(screen.getByText('Tell me about a project.')).toBeInTheDocument() // caption
    expect(screen.getByText('Interview Session')).toBeInTheDocument()        // panel pill
    expect(screen.getByRole('button', { name: /end interview/i })).toBeInTheDocument()
  })
})
