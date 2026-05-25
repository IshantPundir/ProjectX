import * as React from "react";
import { cn } from "@/lib/utils";

export type BadgeVariant =
  | "default"
  | "primary"
  | "ok"
  | "caution"
  | "danger"
  | "ai"
  | "human"
  | "neutral"
  | "secondary"
  | "destructive";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
}

/** Map shadcn-flavored variant names to px semantic classes. */
const VARIANT_MAP: Record<BadgeVariant, string> = {
  default: "",
  secondary: "",
  primary: "primary",
  ok: "ok",
  caution: "caution",
  danger: "danger",
  destructive: "danger",
  ai: "ai",
  human: "human",
  neutral: "neutral",
};

export function Badge({
  variant = "default",
  dot = false,
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      className={cn("px-badge", VARIANT_MAP[variant], className)}
      {...rest}
    >
      {dot && <span className="px-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
