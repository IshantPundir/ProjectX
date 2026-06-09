import { z } from 'zod'

/**
 * Environment schema for the candidate session app.
 *
 * Parsed at module load. Invalid config crashes the app at boot —
 * no fallback, no warning-and-continue. Mirrors backend pydantic-settings
 * discipline.
 */
export const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url(),
  // Dev-only flag. The VisionDebugOverlay renders only when this is true.
  // Treated as a strict opt-in: only the literal "1" enables it.
  NEXT_PUBLIC_PROCTORING_DEBUG: z
    .string()
    .optional()
    .transform((v) => v === '1'),
  // CSP connect-src origin(s) for the LiveKit SFU. The livekit-client opens a
  // WebSocket AND issues an https validate/prepareConnection fetch to the SAME
  // host, so BOTH schemes must be allowed. Defaults to the LiveKit Cloud
  // wildcards (wss + https) for back-compat. Self-hosted prod: set the
  // space-separated pair, e.g. "wss://livekit.example.com https://livekit.example.com".
  NEXT_PUBLIC_LIVEKIT_WS_URL: z
    .string()
    .optional()
    .transform((v) => v && v.length > 0 ? v : 'wss://*.livekit.cloud https://*.livekit.cloud'),
})

export type Env = z.infer<typeof envSchema>

/**
 * Parsed env. Throws z.ZodError at module load if validation fails.
 * Import this from any client/server file that needs env values rather
 * than reading process.env.* directly.
 */
export const env: Env = envSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_PROCTORING_DEBUG: process.env.NEXT_PUBLIC_PROCTORING_DEBUG,
  NEXT_PUBLIC_LIVEKIT_WS_URL: process.env.NEXT_PUBLIC_LIVEKIT_WS_URL,
})
