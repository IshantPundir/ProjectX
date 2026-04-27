"use client";

import Link from "next/link";

/**
 * Generic access-denied panel for admin-only routes. Mirrors the locked
 * org-unit stub style — calm, non-alarmist, surfaces a path back. Rendered
 * inline by pages that gate themselves via `isAnyAdmin(me)`. Routes are
 * also hidden from the nav rail when not admin, so this is the second
 * line of defence (direct URL navigation, bookmarks, etc.).
 */
export function AccessDenied({
  title = "Admins only",
  description = "You don't have admin access on any unit, so this section is hidden. Ask your super admin if you need to manage org units or team members.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="mx-auto max-w-[1200px] px-8 pt-6">
      <div
        className="rounded-[10px] border p-6"
        style={{
          background: "var(--px-bg-2)",
          borderColor: "var(--px-hairline)",
        }}
      >
        <div
          className="mb-2 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: "0.6px", color: "var(--px-fg-4)" }}
        >
          Restricted
        </div>
        <h1
          className="px-serif m-0 text-[26px] font-normal"
          style={{ letterSpacing: "-0.3px", color: "var(--px-fg)" }}
        >
          {title}
        </h1>
        <p
          className="mt-2 text-[13px]"
          style={{ color: "var(--px-fg-3)", lineHeight: 1.55 }}
        >
          {description}
        </p>
        <div className="mt-5">
          <Link
            href="/"
            className="px-btn outline xs"
            style={{ display: "inline-block" }}
          >
            ← Back to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
