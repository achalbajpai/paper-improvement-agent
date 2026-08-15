"use client";

import { Mono } from "@/components/ui/Badge";
import type { ParagraphDiff } from "@/lib/api/client";

export function DiffView({ diff }: { diff: ParagraphDiff }) {
  const before = diff.before_text.split(/(\s+)/);
  const after = diff.after_text.split(/(\s+)/);
  const [removed, added] = align(before, after);

  const citationsAdded = (diff.after_citation_ids ?? []).filter(
    (id) => !(diff.before_citation_ids ?? []).includes(id),
  );
  const citationsRemoved = (diff.before_citation_ids ?? []).filter(
    (id) => !(diff.after_citation_ids ?? []).includes(id),
  );

  return (
    <div className="rounded-card border border-border">
      <div className="flex items-baseline justify-between gap-3 border-b border-border bg-surface-alt px-3 py-2">
        <Mono className="text-text-muted">{diff.paragraph_id}</Mono>
        <span className="tabular text-label text-text-muted">
          {countWords(diff.before_text)} → {countWords(diff.after_text)} words
        </span>
      </div>

      <div className="grid gap-0 md:grid-cols-2">
        <Side label="Before" className="md:border-r md:border-border">
          {before.map((token, index) => (
            <span
              key={index}
              className={removed.has(index) ? "bg-block-tint text-block line-through" : undefined}
            >
              {token}
            </span>
          ))}
        </Side>
        <Side label="After" className="border-t border-border md:border-t-0">
          {after.map((token, index) => (
            <span key={index} className={added.has(index) ? "bg-accent-soft" : undefined}>
              {token}
            </span>
          ))}
        </Side>
      </div>

      {citationsAdded.length > 0 || citationsRemoved.length > 0 ? (
        <div className="border-t border-border px-3 py-2 text-secondary text-text-muted">
          {citationsAdded.length > 0 ? (
            <p>
              Citations added:{" "}
              {citationsAdded.map((id) => (
                <Mono key={id}>{id} </Mono>
              ))}
            </p>
          ) : null}
          {citationsRemoved.length > 0 ? (
            <p>
              Citations removed:{" "}
              {citationsRemoved.map((id) => (
                <Mono key={id}>{id} </Mono>
              ))}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Side({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={className}>
      <p className="px-3 pt-2 text-label uppercase text-text-muted">{label}</p>

      <p className="whitespace-pre-wrap px-3 pb-3 pt-1 text-manuscript">{children}</p>
    </div>
  );
}

export function align(before: string[], after: string[]): [Set<number>, Set<number>] {
  const width = after.length + 1;
  const table = new Uint32Array((before.length + 1) * width);
  const at = (i: number, j: number): number => table[i * width + j] ?? 0;

  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      table[i * width + j] =
        before[i] === after[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1));
    }
  }

  const removed = new Set<number>();
  const added = new Set<number>();
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      i += 1;
      j += 1;
    } else if (at(i + 1, j) >= at(i, j + 1)) {
      removed.add(i);
      i += 1;
    } else {
      added.add(j);
      j += 1;
    }
  }
  for (; i < before.length; i += 1) removed.add(i);
  for (; j < after.length; j += 1) added.add(j);
  return [removed, added];
}

function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}
