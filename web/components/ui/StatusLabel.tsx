import { cn } from "@/lib/cn";

export type Tone = "pass" | "warn" | "block" | "degraded" | "neutral";

const TONES: Record<Tone, string> = {
  pass: "border-border text-text",
  warn: "border-warn/40 bg-warn-tint text-warn",
  block: "border-block/40 bg-block-tint text-block",

  degraded: "border-transparent text-text-muted",
  neutral: "border-border text-text-muted",
};

export function StatusLabel({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-pill border px-2 py-0.5 text-label",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function BlockingNotice({
  title,
  children,
  tone = "block",
}: {
  title: string;
  children?: React.ReactNode;
  tone?: Extract<Tone, "block" | "warn">;
}) {
  return (
    <div
      className={cn(
        "rounded-card border p-4",
        tone === "block" ? "border-block/30 bg-block-tint" : "border-warn/30 bg-warn-tint",
      )}
    >
      <p className={cn("text-card-title", tone === "block" ? "text-block" : "text-warn")}>
        {title}
      </p>
      {children ? <div className="mt-2 text-secondary text-text">{children}</div> : null}
    </div>
  );
}
