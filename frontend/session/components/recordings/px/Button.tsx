import * as React from "react";
import { cn } from "@/lib/utils";

export type ButtonVariant =
  | "primary"
  | "outline"
  | "ghost"
  | "destructive"
  | "secondary"
  | "link";
export type ButtonSize = "default" | "sm" | "xs" | "lg" | "icon" | "icon-sm" | "icon-xs";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  // Back-compat alias for shadcn's "default" variant (primary).
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "px-btn primary",
  outline: "px-btn outline",
  ghost: "px-btn ghost",
  destructive: "px-btn destructive",
  secondary: "px-btn secondary",
  link: "px-btn link",
};

const SIZE_CLASS: Record<ButtonSize, string> = {
  default: "",
  sm: "sm",
  xs: "xs",
  lg: "lg",
  icon: "icon",
  "icon-sm": "icon-sm",
  "icon-xs": "icon-xs",
};

/**
 * Button — built on the .px-btn utility class system.
 *
 * API mirrors shadcn's Button: supports variant + size. The shadcn "default"
 * variant maps to "primary" here (same visual intent).
 */
// Shadcn back-compat: accept "default" as a synonym for "primary".
type AnyVariant = ButtonVariant | "default";

export const Button = React.forwardRef<
  HTMLButtonElement,
  Omit<ButtonProps, "variant"> & { variant?: AnyVariant }
>(function Button(
  { variant = "primary", size = "default", className, type = "button", loading = false, disabled, children, ...rest },
  ref,
) {
  const resolvedVariant: ButtonVariant =
    variant === "default" ? "primary" : variant;
  const variantClass = VARIANT_CLASS[resolvedVariant];
  const sizeClass = SIZE_CLASS[size];
  const isDisabled = disabled || loading;
  const isIconSize = size === "icon" || size === "icon-sm" || size === "icon-xs";
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={cn(variantClass, sizeClass, className)}
      disabled={isDisabled}
      aria-busy={loading || undefined}
    >
      {loading && (
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
          className="px-spin" aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-6.2-8.6" />
        </svg>
      )}
      {isIconSize && loading ? null : children}
    </button>
  );
});
