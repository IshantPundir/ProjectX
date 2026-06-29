"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Tooltip — hover/focus-triggered. CSS-driven via .px-tooltip-root.
 *
 * Simpler than a popover-based solution — appears above the trigger, positioned
 * with a fixed offset. Good enough for inline hints; not suited to very long
 * strings or edge-collision cases.
 */

export function TooltipProvider({
  children,
}: {
  children: React.ReactNode;
  delay?: number;
  delayDuration?: number;
}) {
  return <>{children}</>;
}

export function Tooltip({ children }: { children: React.ReactNode }) {
  return <span className="px-tooltip-root">{children}</span>;
}

/**
 * TooltipTrigger — accepts either children directly or a `render` element
 * (Base UI-style) for shadcn back-compat. If `render` is provided we clone it
 * so consumers can swap in a <span>, <button>, etc.
 */
export function TooltipTrigger({
  children,
  render,
  ...rest
}: {
  children?: React.ReactNode;
  render?: React.ReactElement;
} & React.HTMLAttributes<HTMLSpanElement>) {
  if (render && React.isValidElement(render)) {
    return React.cloneElement(render, rest);
  }
  return <span {...rest}>{children}</span>;
}

export interface TooltipContentProps
  extends React.HTMLAttributes<HTMLSpanElement> {
  side?: "top" | "bottom";
}

export function TooltipContent({
  className,
  side: _side,
  children,
  ...rest
}: TooltipContentProps) {
  return (
    <span
      role="tooltip"
      className={cn("px-tooltip-content", className)}
      {...rest}
    >
      {children}
    </span>
  );
}
