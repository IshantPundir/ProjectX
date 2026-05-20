export interface AppConfig {
  pageTitle: string
  pageDescription: string
  companyName: string

  supportsChatInput: boolean
  supportsVideoInput: boolean
  supportsScreenShare: boolean
  isPreConnectBufferEnabled: boolean

  logo: string
  startButtonText: string
  accent?: string
  logoDark?: string
  accentDark?: string

  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura'
  audioVisualizerColor?: `#${string}`
  audioVisualizerColorDark?: `#${string}`
  audioVisualizerColorShift?: number
  audioVisualizerBarCount?: number
  audioVisualizerGridRowCount?: number
  audioVisualizerGridColumnCount?: number
  audioVisualizerRadialBarCount?: number
  audioVisualizerRadialRadius?: number
  audioVisualizerWaveLineWidth?: number

  agentName?: string
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'ProjectX',
  pageTitle: 'ProjectX · Interview',
  pageDescription: 'AI-led interview',
  supportsChatInput: true,
  supportsVideoInput: true,
  supportsScreenShare: true,
  isPreConnectBufferEnabled: true,
  logo: '/projectx-logo.svg',
  startButtonText: 'Start interview',
  accent: '#8B5CF6',
  audioVisualizerType: 'aura',
  audioVisualizerColorShift: 2,
  agentName: undefined,
}
