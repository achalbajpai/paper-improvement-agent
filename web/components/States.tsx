import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import type { Failure } from "@/lib/useAsync";

export function Loading({ className, label = "Loading…" }: { className?: string; label?: string }) {
  return (
    <p className={cn("text-secondary text-text-muted", className)} role="status">
      {label}
    </p>
  );
}

export function Failed({
  failure,
  onRetry,
  className,
}: {
  failure: Failure;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={cn("rounded-card border border-block/30 bg-block-tint p-4", className)}>
      <p className="text-secondary text-block">{failure.message}</p>
      <div className="mt-2 flex items-center gap-3">
        {onRetry ? (
          <Button variant="quiet" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function Empty({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn("text-secondary text-text-muted", className)}>{children}</p>;
}
