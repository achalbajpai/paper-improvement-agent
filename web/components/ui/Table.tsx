import { cn } from "@/lib/cn";

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-card border border-border", className)}>
      <table className="w-full border-collapse text-body">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return <thead className="bg-surface-alt">{children}</thead>;
}

export function TH({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "border-b border-border px-3 py-2 text-left text-label uppercase text-text-muted",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function TD({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={cn("border-b border-border px-3 py-2 align-top", className)}>{children}</td>
  );
}
