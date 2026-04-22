import * as React from "react";
import { cn } from "@/lib/utils";

export interface SeparatorProps extends React.HTMLAttributes<HTMLHRElement> {
  orientation?: "horizontal" | "vertical";
}

export function Separator({
  orientation = "horizontal",
  className,
  ...rest
}: SeparatorProps) {
  return (
    <hr
      role="separator"
      aria-orientation={orientation}
      className={cn("px-separator", orientation === "vertical" && "vertical", className)}
      {...rest}
    />
  );
}
