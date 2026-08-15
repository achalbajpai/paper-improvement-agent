import type { TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

const FIELD =
  "w-full rounded-control border border-border bg-bg px-3 text-body " +
  "placeholder:text-text-muted focus:border-accent focus:outline-none " +
  "transition-colors duration-state";

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(FIELD, "py-2", className)} {...props} />;
}
