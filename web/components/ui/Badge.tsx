import { cn } from "@/lib/cn";

export function Badge({
  children,
  className,
  mono = false,
}: {
  children: React.ReactNode;
  className?: string;
  mono?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-pill border border-border px-2 py-0.5 text-label text-text-muted",
        mono && "font-mono",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <code className={cn("font-mono text-mono", className)}>{children}</code>;
}
