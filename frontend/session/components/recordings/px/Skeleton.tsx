import * as React from "react";
import { cn } from "@/lib/utils";

export function Skeleton({
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-skeleton", className)} {...rest} />;
}
