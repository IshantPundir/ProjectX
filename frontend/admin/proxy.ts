import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = new Set(["/login", "/signup"]);

export async function proxy(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      auth: { storageKey: "sb-admin-auth-token" },
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const path = request.nextUrl.pathname;

  // Validate session on every request (also refreshes token if needed)
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  // Public paths — redirect to dashboard if already logged in
  if (PUBLIC_PATHS.has(path)) {
    if (user && !error) {
      const isAdmin = user.app_metadata?.is_projectx_admin;
      if (isAdmin) {
        return NextResponse.redirect(new URL("/dashboard", request.url));
      }
      return NextResponse.redirect(new URL("/pending-approval", request.url));
    }
    return supabaseResponse;
  }

  // Not authenticated → login
  if (error || !user) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // /pending-approval — any authenticated user
  if (path === "/pending-approval") {
    return supabaseResponse;
  }

  // All other routes require is_projectx_admin
  const isAdmin = user.app_metadata?.is_projectx_admin;
  if (!isAdmin) {
    return NextResponse.redirect(new URL("/pending-approval", request.url));
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
