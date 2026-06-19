import { Play } from 'lucide-react'
import './report.css'
import type { ReportHeader, Verdict } from '@/lib/api/reports'
import { VerdictStamp } from './VerdictStamp'

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
  onOpenReel: () => void
  onOpenSession: () => void
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ImmersiveHeader({
  header,
  verdict,
  hasReel,
  onOpenReel,
  onOpenSession,
}: ImmersiveHeaderProps) {
  const showReel = hasReel && verdict !== 'reject'

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
          {/* Photo / monogram */}
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

          {/* Identity block */}
          <div className="flex-1">
            <p className="m-0 mb-[3px] text-[30px] font-bold text-white leading-none">
              {header.candidate_name}
            </p>

            {header.candidate_email && (
              <p className="mb-[11px] text-[13px] text-[#c7c8e4]">{header.candidate_email}</p>
            )}

            {/* Inline info row */}
            <div className="flex flex-wrap gap-[22px] text-[12.5px] text-[#cfd0ea]">
              <span>
                💼 <strong className="text-white">{header.job_title}</strong>
                {header.stage_label ? ` · ${header.stage_label}` : ''}
              </span>

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
