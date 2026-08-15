import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("rounded-card border border-border bg-bg p-4", className)}>{children}</div>
  );
}

export function CardTitle({
  className,
  children,
  as: Heading = "h3",
}: {
  className?: string;
  children: ReactNode;
  as?: "h2" | "h3";
}) {
  return <Heading className={cn("text-card-title", className)}>{children}</Heading>;
}

export function CardMeta({ className, children }: { className?: string; children: ReactNode }) {
  return <p className={cn("text-secondary text-text-muted", className)}>{children}</p>;
}
