export function findSnippet(raw: string | null | undefined, needle: string): string | null {
  if (!raw || !needle) return null
  const idx = raw.toLowerCase().indexOf(needle.toLowerCase())
  if (idx < 0) return null
  // Pick the sentence containing the match.
  const start = Math.max(
    raw.lastIndexOf('. ', idx) + 1,
    raw.lastIndexOf('\n', idx) + 1,
    0,
  )
  let end = raw.length
  for (const delim of ['. ', '\n']) {
    const i = raw.indexOf(delim, idx + needle.length)
    if (i > 0 && i < end) end = i + 1
  }
  const slice = raw.slice(start, end).trim()
  return slice.length > 320 ? slice.slice(0, 320) + '…' : slice
}
