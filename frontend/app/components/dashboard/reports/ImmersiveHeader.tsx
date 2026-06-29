import { type CSSProperties, useId } from 'react'
import { Clapperboard, Loader2, Play } from 'lucide-react'
import './report.css'
import type { ReportHeader, Verdict } from '@/lib/api/reports'
import { VerdictStamp } from './VerdictStamp'

// ─── Verified seal badge ───────────────────────────────────────────────────────
// Scalloped blue seal + white check, with a glossy 3D look (top-lit gradient,
// highlight sheen, embossed check). Outer lift comes from a CSS drop-shadow.
function VerifiedBadge({ size = 36 }: { size?: number }) {
  const uid = useId().replace(/[:]/g, '')
  const grad = `vb-grad-${uid}`
  const gloss = `vb-gloss-${uid}`

  // 12-point star whose spikes get rounded into scallops by a thick round stroke.
  const cx = 50, cy = 50, spikes = 12, outer = 31, inner = 25
  const step = Math.PI / spikes
  let rot = -Math.PI / 2
  let d = ''
  for (let i = 0; i < spikes * 2; i++) {
    const r = i % 2 === 0 ? outer : inner
    const x = cx + Math.cos(rot) * r
    const y = cy + Math.sin(rot) * r
    d += `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    rot += step
  }
  d += 'Z'

  const check = 'M36 50.5 L46 60.5 L67 38.5'

  return (
    <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label="Identity verified">
      <defs>
        <linearGradient id={grad} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#5cb3ff" />
          <stop offset="52%" stopColor="#2b8ff0" />
          <stop offset="100%" stopColor="#1466d6" />
        </linearGradient>
        <radialGradient id={gloss} cx="50%" cy="26%" r="62%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* seal body — fill + thick round stroke rounds the spikes into scallops */}
      <path d={d} fill={`url(#${grad})`} stroke={`url(#${grad})`} strokeWidth="17"
        strokeLinejoin="round" strokeLinecap="round" />
      {/* top glossy sheen for convex depth */}
      <path d={d} fill={`url(#${gloss})`} stroke={`url(#${gloss})`} strokeWidth="17"
        strokeLinejoin="round" strokeLinecap="round" />
      {/* embossed check: darker drop beneath, white on top */}
      <path d={check} fill="none" stroke="#0c4ea8" strokeOpacity="0.5" strokeWidth="9"
        strokeLinecap="round" strokeLinejoin="round" transform="translate(0,1.6)" />
      <path d={check} fill="none" stroke="#ffffff" strokeWidth="9"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ─── Verdict-based glow around the candidate photo ─────────────────────────────
// glow = soft radial halo behind the photo; ring = the photo's edge ring.
const VERDICT_GLOW: Record<Verdict, { glow: string; ring: string }> = {
  advance: { glow: 'rgba(54,208,127,0.60)', ring: 'rgba(54,208,127,0.55)' },
  borderline: { glow: 'rgba(245,176,69,0.60)', ring: 'rgba(245,176,69,0.55)' },
  reject: { glow: 'rgba(239,68,68,0.58)', ring: 'rgba(239,68,68,0.52)' },
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('')
}

function formatSessionDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ImmersiveHeaderProps {
  header: ReportHeader
  verdict: Verdict
  hasReel: boolean
  /** Backend says a reel can be generated (report + recording ready, any verdict). */
  reelEligible: boolean
  /** A reel is currently being generated (pending/generating or the mutation is in flight). */
  reelBusy: boolean
  onOpenReel: () => void
  onGenerateReel: () => void
  onOpenSession: () => void
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ImmersiveHeader({
  header,
  verdict,
  hasReel,
  reelEligible,
  reelBusy,
  onOpenReel,
  onGenerateReel,
  onOpenSession,
}: ImmersiveHeaderProps) {
  // Evidence Reel is available for every verdict; the backend `reelEligible`
  // flag already gates on report + recording readiness.
  const showReel = hasReel
  const showGenerate = !hasReel && reelEligible

  return (
    <div className="rh-hero overflow-hidden rounded-[18px] shadow-[0_12px_40px_rgba(20,20,40,.22)]">
      {/* ── Atmospheric background ── */}
      <div className="rh-hero-in relative p-[30px_32px_26px]">
        {/* dot-grid overlay */}
        <div className="rh-dot-grid" aria-hidden />

        {/* ── Top bar: wordmark + confidential tag ── */}
        <div className="relative mb-[22px] flex items-start justify-between">
          <span className="text-[18px] font-extrabold text-white">
            BinQle<span className="text-[#a7a3f5]">.ai</span>
          </span>
          <span className="text-right text-[9px] uppercase tracking-[2px] text-[#9b9cc4] leading-[1.9]">
            Candidate Evaluation
            <br />
            Confidential
          </span>
        </div>

        {/* ── Main row: photo + identity + stamp ── */}
        <div className="relative flex items-center gap-[26px]">
          {/* Photo / monogram — wrapped with a verdict-colored gradient glow */}
          <div
            className="rh-photo-wrap"
            style={{
              '--rh-glow': VERDICT_GLOW[verdict].glow,
              '--rh-ring': VERDICT_GLOW[verdict].ring,
            } as CSSProperties}
          >
            {header.reference_photo_url ? (
              <img
                src={header.reference_photo_url}
                alt={header.candidate_name}
                className="rh-photo"
              />
            ) : (
              <div className="rh-photo rh-monogram" aria-label={`${header.candidate_name} initials`}>
                {initials(header.candidate_name)}
              </div>
            )}

            {/* Verified badge — identity confirmed (OTP + consent) for this session */}
            <span className="rh-verified" title="Identity verified">
              <VerifiedBadge size={38} />
            </span>
          </div>

          {/* Identity block */}
          <div className="flex-1">
            <p className="m-0 mb-[3px] text-[30px] font-bold text-white leading-none">
              {header.candidate_name}
            </p>

            {(header.candidate_email || header.candidate_title || header.candidate_location) && (
              <div className="mb-[11px] space-y-[3px]">
                {header.candidate_email && (
                  <p className="text-[13px] text-[#c7c8e4]">{header.candidate_email}</p>
                )}
                {(header.candidate_title || header.candidate_location) && (
                  <p className="text-[12.5px] text-[#b9bae0]">
                    {[
                      header.candidate_title,
                      header.candidate_location && `📍 ${header.candidate_location}`,
                    ].filter(Boolean).join('   ·   ')}
                  </p>
                )}
              </div>
            )}

            {/* Inline info row */}
            <div className="flex flex-wrap gap-[22px] text-[12.5px] text-[#cfd0ea]">
              <span>
                💼 <strong className="text-white">{header.job_title}</strong>
                {header.company_name ? ` · ${header.company_name}` : ''}
                {header.stage_label ? ` · ${header.stage_label}` : ''}
              </span>

              {(header.work_arrangement || header.job_location) && (
                <span>
                  🌐 {[header.work_arrangement, header.job_location].filter(Boolean).join(' · ')}
                </span>
              )}

              {header.session_started_at && (
                <span>🗓 {formatSessionDate(header.session_started_at)}</span>
              )}

              {header.duration_seconds != null && (
                <span>⏱ {formatDuration(header.duration_seconds)}</span>
              )}
            </div>

            {/* CTA row */}
            <div className="mt-5 flex items-center gap-[13px]">
              {showReel && (
                <button
                  type="button"
                  aria-label="Candidate highlight"
                  onClick={onOpenReel}
                  className="rh-btn-reel inline-flex items-center gap-[9px] cursor-pointer"
                >
                  <Play size={14} aria-hidden />
                  Candidate highlight
                </button>
              )}

              {showGenerate && (
                <button
                  type="button"
                  aria-label="Generate Evidence Reel"
                  onClick={onGenerateReel}
                  disabled={reelBusy}
                  aria-busy={reelBusy}
                  className="rh-btn-gen inline-flex items-center gap-[9px] cursor-pointer disabled:cursor-default"
                >
                  {reelBusy ? (
                    <>
                      <Loader2 size={14} aria-hidden className="animate-spin" />
                      Generating…
                    </>
                  ) : (
                    <>
                      <Clapperboard size={14} aria-hidden />
                      Generate Evidence Reel
                    </>
                  )}
                </button>
              )}

              <button
                type="button"
                aria-label="Full session recording"
                onClick={onOpenSession}
                className="rh-btn-sec inline-flex items-center gap-[9px] cursor-pointer"
              >
                <Play size={14} aria-hidden />
                Full session
              </button>
            </div>
          </div>

          {/* Verdict stamp */}
          <VerdictStamp verdict={verdict} />
        </div>

        {/* ── Skills ── */}
        {header.skills.length > 0 && (
          <>
            <p className="relative mt-[18px] mb-[5px] text-[9.5px] uppercase tracking-[1.5px] text-[#9b9cc4]">
              Skills demonstrated
            </p>
            <div className="relative flex flex-wrap">
              {header.skills.map((skill) => (
                <span key={skill} className="rh-pill">
                  {skill}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
