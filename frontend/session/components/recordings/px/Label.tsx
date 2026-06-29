import * as React from "react";
import { cn } from "@/lib/utils";

export const Label = React.forwardRef<
  HTMLLabelElement,
  React.LabelHTMLAttributes<HTMLLabelElement>
>(function Label({ className, ...rest }, ref) {
  return <label ref={ref} className={cn("px-label", className)} {...rest} />;
});
