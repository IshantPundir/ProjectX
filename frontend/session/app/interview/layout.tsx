import type { CSSProperties, ReactNode } from "react";

import { AnimatedBackground } from "@/components/agents-ui/animated-background";
import { DevtoolsShield } from "@/components/DevtoolsShield";

/**
 * Interview-scoped layout — wraps every route under /interview/*.
 *
 * Hosts the candidate-interview-only chrome that must NOT bleed into
 * the public /recordings/* page:
 *
 *   - The --px-app-base / --px-accent background wrapper (the cool-light
 *     ambient surface behind the wizard and live session UI).
 *   - AnimatedBackground — the drifting blob layer.
 *   - DevtoolsShield — blocks right-click / devtools shortcuts and covers
 *     the page if devtools is detected open.  On the public recordings page
 *     this shield would strip the external recruiter's native <video>
 *     context-menu (picture-in-picture / playback-speed / download) and
 *     block devtools — both are unacceptable there.
 *
 * The root layout (app/layout.tsx) remains minimal: html/body/fonts/
 * theme attrs + InterviewProviders (QueryClient + Toaster, both benign
 * on any route).
 */
export default function InterviewLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <div
        className="min-h-screen w-full"
        style={
          {
            // --px-accent default; per-tenant override applied closer to the surface later.
            "--px-accent": "#8B5CF6",
            background: "var(--px-app-base)",
            color: "var(--px-fg)",
          } as CSSProperties
        }
      >
        <AnimatedBackground />
        {children}
      </div>
      {/* Candidate devtools deterrent — scoped to /interview/* only.
          Blocks open-shortcuts/right-click and covers the page if
          devtools is detected open during the interview. */}
      <DevtoolsShield />
    </>
  );
}
