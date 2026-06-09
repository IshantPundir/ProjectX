import { NextResponse, type NextRequest } from "next/server";

/**
 * Per-request CSP via nonce + 'strict-dynamic'.
 *
 * Static next.config.ts headers can't carry a per-request nonce, and a
 * strict `script-src 'self'` blocks Next.js's own inline bootstrap
 * script (`__next_r`) — that's what made the candidate page hang on a
 * stuck "Loading…" spinner during the first manual smoke. Per the Next
 * 16 CSP guide (`node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md`),
 * the canonical pattern is middleware-generated nonce + 'strict-dynamic'.
 *
 * Dev mode requires `'unsafe-eval'` (React's enhanced debugger uses
 * `eval` to reconstruct server stacks) and `'unsafe-inline'` style
 * fallback (Tailwind's dev runtime injects inline styles before the
 * stylesheet hydrates). Neither is permitted in production.
 *
 * `Referrer-Policy: no-referrer` is load-bearing — the candidate JWT
 * lives in the URL path, and `Referer` headers would leak it to any
 * external link the page resolves. That header lives in next.config.ts
 * (static) alongside HSTS / X-Frame-Options / Permissions-Policy /
 * COOP / CORP. Only CSP needs the nonce, so only CSP lives here.
 *
 * The matcher excludes static assets and prefetches so the nonce is
 * only generated when an actual page render happens.
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";

  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    // Same boundary as next.config.ts — refuse to serve a request
    // without a known API origin baked into connect-src.
    throw new Error(
      "NEXT_PUBLIC_API_URL must be set; the candidate session app cannot serve requests without a known backend origin in CSP connect-src.",
    );
  }

  const cspHeader = [
    "default-src 'self'",
    // 'wasm-unsafe-eval' enables MediaPipe tasks-vision WASM compilation
    // (same-origin /mediapipe/wasm). Narrower than 'unsafe-eval'. See
    // docs/superpowers/specs/2026-05-29-vision-proctoring-design.md §5.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'wasm-unsafe-eval'${isDev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' ${isDev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    "img-src 'self' data: blob:",
    "media-src 'self' blob: mediastream:",
    "font-src 'self' data:",
    // connect-src LiveKit origin: keep this fallback in sync with the
    // NEXT_PUBLIC_LIVEKIT_WS_URL transform default in lib/env.ts (proxy runs
    // in edge runtime and can't import the parsed env object).
    `connect-src 'self' ${apiUrl}${isDev ? " ws://localhost:*" : ""} ${process.env.NEXT_PUBLIC_LIVEKIT_WS_URL ?? "wss://*.livekit.cloud https://*.livekit.cloud"}`,
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    "upgrade-insecure-requests",
  ]
    .join("; ")
    .trim();

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", cspHeader);

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });
  response.headers.set("Content-Security-Policy", cspHeader);

  return response;
}

export const config = {
  matcher: [
    {
      // Exclude static assets and prefetches — they don't need a per-request CSP nonce.
      source: "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
