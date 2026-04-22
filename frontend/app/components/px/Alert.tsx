import * as React from "react";
import { cn } from "@/lib/utils";

export type AlertVariant = "default" | "destructive" | "caution" | "ok";

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant;
}

export function Alert({
  variant = "default",
  className,
  children,
  role = "alert",
  ...rest
}: AlertProps) {
  const variantClass = variant === "default" ? "" : variant;
  return (
    <div role={role} className={cn("px-alert", variantClass, className)} {...rest}>
      {children}
    </div>
  );
}

export function AlertTitle({
  className,
  ...rest
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h5 className={cn("px-alert-title", className)} {...rest} />;
}

export function AlertDescription({
  className,
  ...rest
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return <div className={cn("px-alert-description", className)} {...rest} />;
}
