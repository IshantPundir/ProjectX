/**
 * Reserves the media slot the session recording (sub-project B) will fill.
 * Self-contained so B can replace its internals with a real player without
 * touching the surrounding layout.
 */
export function SessionPlaybackStub() {
  return (
    <div className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
      <div
        className="relative flex flex-col items-center justify-center rounded-lg"
        style={{ aspectRatio: '16 / 7', background: 'linear-gradient(160deg,#22323b,#0C2A38)', border: '1px dashed rgba(255,255,255,0.18)' }}
      >
        <span className="absolute right-2.5 top-2.5 rounded px-1.5 py-0.5 text-[9px]" style={{ background: 'rgba(255,255,255,0.12)', color: '#cdd9df' }}>
          Sub-project B
        </span>
        <span className="text-[30px]" aria-hidden="true">🎬</span>
        <span className="mt-1.5 text-[12px] font-semibold" style={{ color: '#c4d2d9' }}>Session playback</span>
        <span className="mt-0.5 text-[10.5px]" style={{ color: '#7d929c' }}>Recording &amp; video playback arrive with the recording feature</span>
      </div>
      <VerbalContentOnlyBadge />
    </div>
  )
}

export function VerbalContentOnlyBadge() {
  return (
    <div className="mt-2.5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px]"
      style={{ color: 'var(--px-ai)', background: 'var(--px-ai-bg)', borderColor: 'var(--px-ai-line)' }}>
      🛈&nbsp;Verbal-content-only — scored on what the candidate said. No facial, affect, or appearance analysis.
    </div>
  )
}
