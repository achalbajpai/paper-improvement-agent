import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "quiet";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-bg border border-accent hover:opacity-90",
  secondary: "bg-bg text-text border border-border hover:border-border-strong",
  quiet: "bg-transparent text-text-muted border border-transparent hover:text-text",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export function Button({ variant = "secondary", className, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-control px-3 text-secondary font-medium",
        "transition-colors duration-state disabled:cursor-not-allowed disabled:opacity-45",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
