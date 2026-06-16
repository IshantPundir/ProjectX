'use client'

/**
 * Multiple-faces warning for the camera step — styled like the in-session
 * ViolationNoticeOverlay so the candidate sees the same treatment they would
 * during the interview. Rendered only while more than one face is in frame;
 * it clears itself when the candidate is alone again (no dismiss button, no
 * reporting — there is no session yet). `absolute` so it stays inside the
 * immersive camera view.
 */
export function PreCheckFaceWarning() {
  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      aria-label="Multiple people detected"
      className="absolute inset-0 z-[20] grid place-items-center bg-black/65 p-6 text-center backdrop-blur-md"
    >
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10">
        <h2 className="px-serif text-2xl font-normal text-px-fg">More than one person detected</h2>
        <p className="mt-3 text-sm leading-relaxed text-px-fg-3">
          Only you should be on camera. Ask anyone else to step out of view — the interview
          can&apos;t start until you&apos;re the only person in frame.
        </p>
      </div>
    </div>
  )
}
