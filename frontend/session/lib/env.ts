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
})

export type Env = z.infer<typeof envSchema>

/**
 * Parsed env. Throws z.ZodError at module load if validation fails.
 * Import this from any client/server file that needs env values rather
 * than reading process.env.* directly.
 */
export const env: Env = envSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
})
