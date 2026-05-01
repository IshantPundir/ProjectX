import { NextResponse } from "next/server";

// Liveness probe — must hit the live process every time. force-dynamic
// disables Next's build-time static rendering so a stale CDN/edge cache
// can never report 200 while the process is dead.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ status: "ok", service: "frontend-session" });
}
