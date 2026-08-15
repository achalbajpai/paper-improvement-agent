"use client";

import { useMemo, useState } from "react";

import { Empty, Failed, Loading } from "@/components/States";
import { CardMeta } from "@/components/ui/Card";
import { FilterChips, type FilterOption } from "@/components/ui/FilterChips";
import { StatusLabel } from "@/components/ui/StatusLabel";
import { type Manuscript, type Reference } from "@/lib/api/client";
import { humanise, plural } from "@/lib/labels";
import type { AsyncResult } from "@/lib/useAsync";

type Group = "all" | "matched" | "unmatched" | "uncited";

export function ReferencesPanel({ manuscript }: { manuscript: AsyncResult<Manuscript> }) {
  const [group, setGroup] = useState<Group>("all");

  const references = useMemo(() => manuscript.data?.references ?? [], [manuscript.data]);

  const counts = useMemo(
    () => ({
      all: references.length,
      matched: references.filter((item) => item.resolution_method !== "UNRESOLVED").length,
      unmatched: references.filter((item) => item.resolution_method === "UNRESOLVED").length,
      uncited: references.filter((item) => item.occurrences === 0).length,
    }),
    [references],
  );

  if (manuscript.loading && !manuscript.data) return <Loading label="Loading references" />;
  if (manuscript.failure)
    return <Failed failure={manuscript.failure} onRetry={manuscript.reload} />;
  if (references.length === 0) return <Empty>No references found.</Empty>;

  const options: FilterOption<Group>[] = [
    { id: "all", label: "All", count: counts.all },
    { id: "matched", label: "Matched", count: counts.matched, tone: "pass" },
    { id: "unmatched", label: "Not matched", count: counts.unmatched, tone: "warn" },
    { id: "uncited", label: "Uncited", count: counts.uncited },
  ];

  return (
    <section>
      <h2 className="text-section-title">References</h2>
      <CardMeta className="mt-1">
        How each entry was parsed, and whether it was matched to a provider record. An entry that
        was never matched is why some citations could not be checked.
      </CardMeta>

      <FilterChips
        className="mt-4"
        label="Filter references"
        options={options}
        active={group}
        onSelect={setGroup}
      />

      <div
        role="region"
        aria-label="Bibliography entries"
        tabIndex={0}
        className="tabular mt-3 overflow-x-auto rounded-card border border-border"
      >
        <div className="min-w-[560px]">
          <div className="grid grid-cols-[2.2fr_0.9fr_1fr_0.5fr] border-b border-border bg-surface-alt px-3 py-2 text-label uppercase text-text-muted">
            <span>Entry</span>
            <span>Parsed</span>
            <span>Matched</span>
            <span className="text-right">Cites</span>
          </div>

          {references.map((reference) => (
            <Row key={reference.id} reference={reference} shown={matches(reference, group)} />
          ))}
        </div>
      </div>
    </section>
  );
}

function matches(reference: Reference, group: Group) {
  if (group === "all") return true;
  if (group === "matched") return reference.resolution_method !== "UNRESOLVED";
  if (group === "unmatched") return reference.resolution_method === "UNRESOLVED";
  return reference.occurrences === 0;
}

function Row({ reference, shown }: { reference: Reference; shown: boolean }) {
  const unresolved = reference.resolution_method === "UNRESOLVED";
  const byline = [
    (reference.authors ?? []).slice(0, 3).join(", "),
    reference.year?.toString(),
    reference.container_title,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className="collapsible grid"
      style={{ gridTemplateRows: shown ? "1fr" : "0fr", opacity: shown ? 1 : 0 }}
      aria-hidden={!shown}
    >
      <div className="overflow-hidden">
        <div className="grid grid-cols-[2.2fr_0.9fr_1fr_0.5fr] items-baseline border-b border-border px-3 py-2 last:border-b-0">
          <div className="min-w-0 pr-3">
            <p className="truncate text-secondary text-text">
              {reference.title ?? reference.raw_text.slice(0, 200) ?? "Untitled"}
            </p>
            {byline ? <p className="mt-0.5 truncate text-label text-text-muted">{byline}</p> : null}
            {reference.doi ? (
              <a
                className="mt-1 inline-flex min-h-[24px] items-center text-label text-text-muted underline underline-offset-2"
                href={`https://doi.org/${reference.doi}`}
                target="_blank"
                rel="noreferrer noopener"
                aria-label={`Open DOI ${reference.doi}`}
              >
                {reference.doi}
              </a>
            ) : null}
          </div>

          <span>
            <StatusLabel tone={reference.normalization_status === "COMPLETE" ? "pass" : "warn"}>
              {humanise(reference.normalization_status)}
            </StatusLabel>
          </span>

          <span>
            <StatusLabel tone={unresolved ? "warn" : "neutral"}>
              {unresolved ? "Not matched" : humanise(reference.resolution_method)}
            </StatusLabel>
          </span>

          <span
            className="text-right text-secondary text-text-muted"
            title={plural(reference.occurrences, "citation")}
          >
            {reference.occurrences}
          </span>
        </div>
      </div>
    </div>
  );
}
