import type { FieldValues, Path, UseFormReturn } from 'react-hook-form'

import { ApiValidationError } from './client'

/**
 * Apply a thrown error to a react-hook-form instance.
 *
 * Returns `true` if at least one field-level error was set (caller
 * suppresses the toast). Returns `false` for any error shape that is
 * not an `ApiValidationError` — caller falls back to a generic toast
 * or form-level error.
 *
 * Loc handling:
 * - FastAPI prepends `"body"` to every `loc`. Default `stripPrefixes`
 *   is `["body"]` — that segment is dropped greedily from the front of
 *   each loc.
 * - Pass `stripPrefixes: ["body", "metadata"]` (or any other segments)
 *   when the backend nests the request body under a key the frontend
 *   form does NOT mirror. Stripping is greedy: consecutive segments
 *   that match any string in the list are all removed.
 * - The remaining segments are joined with `.` to produce an RHF path
 *   (e.g. `["profile", "about"]` → `"profile.about"`).
 * - If the resulting path is not a known field on the form, the error
 *   falls back to `opts.fallbackFieldKey` (if provided) or `root`.
 */
export function applyApiErrorToForm<T extends FieldValues>(
  err: unknown,
  form: UseFormReturn<T>,
  opts: { fallbackFieldKey?: Path<T>; stripPrefixes?: string[] } = {},
): boolean {
  if (!(err instanceof ApiValidationError)) return false

  const stripPrefixes = opts.stripPrefixes ?? ['body']
  const knownFieldKeys = collectFieldKeys(form.getValues())
  let mappedAny = false

  for (const entry of err.fieldErrors) {
    const path = locToPath(entry.loc, stripPrefixes)
    if (path && knownFieldKeys.has(path)) {
      form.setError(path as Path<T>, { message: entry.msg, type: 'server' })
      mappedAny = true
      continue
    }
    if (opts.fallbackFieldKey) {
      form.setError(opts.fallbackFieldKey, {
        message: entry.msg,
        type: 'server',
      })
      mappedAny = true
      continue
    }
    form.setError('root' as Path<T>, { message: entry.msg, type: 'server' })
    mappedAny = true
  }

  return mappedAny
}

/**
 * Greedily drop leading `stripPrefixes` segments, then join the rest
 * with `.`. Returns null for shapes we don't recognise (e.g. empty
 * after strip).
 */
function locToPath(
  loc: (string | number)[],
  stripPrefixes: string[],
): string | null {
  let stripped = loc
  while (
    stripped.length > 0 &&
    typeof stripped[0] === 'string' &&
    stripPrefixes.includes(stripped[0] as string)
  ) {
    stripped = stripped.slice(1)
  }
  if (stripped.length === 0) return null
  return stripped.map((seg) => String(seg)).join('.')
}

/**
 * Walk the form's current values to collect every valid dotted path.
 * Used to decide whether a server `loc` maps to a known field or
 * should fall through to the fallback slot.
 */
function collectFieldKeys(values: unknown, prefix = ''): Set<string> {
  const keys = new Set<string>()
  if (values === null || typeof values !== 'object' || Array.isArray(values)) {
    return keys
  }
  for (const [k, v] of Object.entries(values as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k
    keys.add(path)
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      for (const nested of collectFieldKeys(v, path)) keys.add(nested)
    }
  }
  return keys
}
