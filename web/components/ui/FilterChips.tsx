"use client";

import { cn } from "@/lib/cn";
import type { Tone } from "@/components/ui/StatusLabel";

export interface FilterOption<T extends string> {
  id: T;
  label: string;
  count: number;
  tone?: Extract<Tone, "pass" | "warn" | "block">;
}

const DOT: Record<"pass" | "warn" | "block", string> = {
  pass: "bg-text-muted",
  warn: "bg-warn",
  block: "bg-block",
};

export function FilterChips<T extends string>({
  options,
  active,
  onSelect,
  label,
  className,
}: {
  options: FilterOption<T>[];
  active: T;
  onSelect: (id: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("-mx-1 flex flex-wrap items-center gap-1 px-1", className)}
    >
      {options.map((option) => {
        const selected = option.id === active;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelect(option.id)}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-2 rounded-pill border px-3",
              "text-secondary font-medium transition-colors duration-state",
              selected
                ? "border-border-strong bg-surface-alt text-text"
                : "border-border text-text-muted hover:border-border-strong hover:text-text",
            )}
          >
            {option.tone ? (
              <span aria-hidden className={cn("size-1.5 rounded-pill", DOT[option.tone])} />
            ) : null}
            {option.label}
            <span className="tabular text-label text-text-muted">{option.count}</span>
          </button>
        );
      })}
    </div>
  );
}
