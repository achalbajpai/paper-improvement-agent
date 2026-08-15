"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Empty, Failed, Loading } from "@/components/States";
import { Badge } from "@/components/ui/Badge";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { StatusLabel, type Tone } from "@/components/ui/StatusLabel";
import {
  type Finding,
  type InlineRun,
  type Manuscript,
  type ManuscriptParagraph,
  type ManuscriptSection,
  type Reference,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/labels";
import type { AsyncResult } from "@/lib/useAsync";

import { FindingCard } from "./FindingCard";

export function ManuscriptView({
  manuscript,
  findings = [],
  target = null,
}: {
  manuscript: AsyncResult<Manuscript>;
  findings?: Finding[];
  target?: string | null;
}) {
  const byParagraph = useMemo(() => {
    const map = new Map<string, Finding[]>();
    for (const finding of findings) {
      if (!finding.claim.paragraph_id) continue;
      const existing = map.get(finding.claim.paragraph_id);
      if (existing) existing.push(finding);
      else map.set(finding.claim.paragraph_id, [finding]);
    }
    return map;
  }, [findings]);

  if (manuscript.loading && !manuscript.data) return <Loading />;
  if (manuscript.failure)
    return <Failed failure={manuscript.failure} onRetry={manuscript.reload} />;
  if (!manuscript.data) return <Empty>Nothing parsed yet.</Empty>;

  const data = manuscript.data;
  const references = data.references ?? [];
  const sections = data.sections ?? [];
  const unlinked = data.unlinked_citation_ids ?? [];
  const rawOnly = data.raw_only_citation_ids ?? [];
  const bibliography = new Map(references.map((reference) => [reference.id, reference]));

  return (
    <section>
      <div>
        <h2 className="text-section-title">The manuscript</h2>
        <CardMeta className="mt-1">
          Revision {data.revision_number}. Citations are marked where they appear. Anything we could
          not resolve is flagged.
        </CardMeta>
      </div>

      {unlinked.length > 0 || rawOnly.length > 0 ? (
        <p className="mt-3 text-secondary text-text-muted">
          {unlinked.length > 0
            ? `${plural(unlinked.length, "marker")} could not be matched to a bibliography entry. `
            : ""}
          {rawOnly.length > 0 ? `${plural(rawOnly.length, "marker")} kept as raw text only.` : ""}
        </p>
      ) : null}

      <article className="mt-6">
        {data.abstract ? (
          <Card>
            <CardTitle>Abstract</CardTitle>
            <p className="mt-2 whitespace-pre-line text-body text-text">{data.abstract}</p>
          </Card>
        ) : null}

        <div className="mt-4 flex flex-col gap-3">
          {sections.map((section, index) => (
            <SectionBlock
              key={section.id}
              section={section}
              bibliography={bibliography}
              byParagraph={byParagraph}
              target={target}
              startOpen={index === 0}
            />
          ))}
        </div>
      </article>
    </section>
  );
}

function SectionBlock({
  section,
  bibliography,
  byParagraph,
  target,
  startOpen = false,
}: {
  section: ManuscriptSection;
  bibliography: Map<string, Reference>;
  byParagraph: Map<string, Finding[]>;
  target: string | null;
  startOpen?: boolean;
}) {
  const paragraphs = section.paragraphs ?? [];
  const findingCount = paragraphs.reduce(
    (total, paragraph) => total + (byParagraph.get(paragraph.id)?.length ?? 0),
    0,
  );
  const citationCount = paragraphs.reduce(
    (total, paragraph) =>
      total + (paragraph.inlines ?? []).filter((run) => run.kind === "citation").length,
    0,
  );
  const [collapsed, setCollapsed] = useState(!startOpen);
  const holdsTarget = target !== null && paragraphs.some((paragraph) => paragraph.id === target);
  const open = holdsTarget || !collapsed;

  return (
    <Card>
      <button
        type="button"
        className="flex min-h-[32px] w-full items-center justify-between gap-4 py-1 text-left"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={open}
      >
        <CardTitle>{section.title || "Untitled section"}</CardTitle>
        <span className="flex shrink-0 items-center gap-2">
          {findingCount > 0 ? (
            <StatusLabel tone="warn">{plural(findingCount, "finding")}</StatusLabel>
          ) : null}
          {citationCount > 0 ? <Badge>{plural(citationCount, "citation")}</Badge> : null}
          <Badge>{plural(paragraphs.length, "paragraph")}</Badge>
        </span>
      </button>

      {open ? (
        <div className="mt-3 flex flex-col gap-4">
          {paragraphs.map((paragraph) => (
            <ParagraphBlock
              key={paragraph.id}
              paragraph={paragraph}
              bibliography={bibliography}
              findings={byParagraph.get(paragraph.id) ?? []}
              targeted={paragraph.id === target}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function ParagraphBlock({
  paragraph,
  bibliography,
  findings,
  targeted,
}: {
  paragraph: ManuscriptParagraph;
  bibliography: Map<string, Reference>;
  findings: Finding[];
  targeted: boolean;
}) {
  const node = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!targeted || !node.current) return;
    node.current.scrollIntoView({ behavior: "smooth", block: "center" });
    node.current.focus({ preventScroll: true });
  }, [targeted]);

  return (
    <div
      id={paragraph.id}
      ref={node}
      tabIndex={targeted ? -1 : undefined}
      className={cn(
        "rounded-control transition-colors duration-state",
        findings.length > 0 ? "border-l-2 border-warn/40 pl-3" : undefined,
        targeted ? "bg-accent-soft" : undefined,
      )}
    >
      <p className="text-body text-text">
        {(paragraph.inlines ?? []).map((run, index) => (
          <InlineNode key={`${paragraph.id}-${index}`} run={run} bibliography={bibliography} />
        ))}
      </p>

      {findings.length > 0 ? (
        <div className="mt-3 flex flex-col gap-2">
          {findings.map((finding) => (
            <FindingCard key={finding.id} finding={finding} compact />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function InlineNode({
  run,
  bibliography,
}: {
  run: InlineRun;
  bibliography: Map<string, Reference>;
}) {
  if (run.kind === "text" || !run.citation) return <>{run.text}</>;

  const citation = run.citation;
  const references = (citation.reference_ids ?? [])
    .map((id) => bibliography.get(id))
    .filter((reference): reference is Reference => Boolean(reference));

  const tone: Tone = citation.is_unlinked
    ? "block"
    : citation.fidelity_exportable
      ? "pass"
      : "warn";
  const title = citation.is_unlinked
    ? "We could not match this to a reference."
    : references.length > 0
      ? references.map((reference) => reference.title ?? reference.raw_text).join("; ")
      : undefined;

  return (
    <span
      title={title}
      className={
        tone === "block"
          ? "rounded-sm bg-block-tint px-0.5 text-block"
          : tone === "warn"
            ? "rounded-sm bg-warn-tint px-0.5 text-warn"
            : "text-accent"
      }
    >
      {citation.raw_marker}
    </span>
  );
}
