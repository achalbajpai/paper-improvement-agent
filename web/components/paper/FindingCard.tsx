"use client";

import { Mono } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { StatusLabel, type Tone } from "@/components/ui/StatusLabel";
import type { Finding, Schemas, SuggestedWork } from "@/lib/api/client";
import { FINDING_LABEL, humanise, plural, VERDICT_LABEL } from "@/lib/labels";

type Verdict = Schemas["SupportVerdict"];

export const VERDICT_TONE: Record<Verdict, Tone> = {
  SUPPORTED: "pass",
  PARTIALLY_SUPPORTED: "neutral",
  CONTRADICTED: "block",
  UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE: "neutral",
  EVIDENCE_UNAVAILABLE: "neutral",
  SOURCE_IDENTITY_UNCERTAIN: "neutral",
  SOURCE_UNRESOLVED: "neutral",
};

export const MODEL_SELECTABLE: ReadonlySet<Verdict> = new Set<Verdict>([
  "SUPPORTED",
  "PARTIALLY_SUPPORTED",
  "CONTRADICTED",
  "UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE",
]);

export function FindingCard({
  finding,
  compact = false,
  onView,
  onHandle,
}: {
  finding: Finding;
  compact?: boolean;
  onView?: (finding: Finding) => void;
  onHandle?: (finding: Finding, handled: boolean) => void;
}) {
  const suggestions = finding.suggestions ?? [];
  const serverDecided = finding.verdict ? !MODEL_SELECTABLE.has(finding.verdict) : false;

  return (
    <Card className={finding.handled ? "opacity-55" : undefined}>
      <div className="flex items-baseline justify-between gap-4">
        <CardTitle>{FINDING_LABEL[finding.kind]}</CardTitle>
        {finding.verdict ? (
          <StatusLabel tone={VERDICT_TONE[finding.verdict]}>
            {VERDICT_LABEL[finding.verdict]}
          </StatusLabel>
        ) : null}
      </div>

      {compact ? null : (
        <CardMeta className="mt-1">
          {finding.claim.section_title ?? "Untitled section"}
          {finding.reference_label ? ` · cites ${finding.reference_label}` : ""}
          {finding.verdict ? (
            <>
              {" · "}
              {serverDecided ? "determined by this server" : "a model reading of the abstract"}
            </>
          ) : null}
        </CardMeta>
      )}

      {finding.claim.text ? (
        <blockquote className="mt-3 border-l-2 border-border pl-3 text-manuscript">
          {finding.claim.text}
        </blockquote>
      ) : null}

      {finding.reason ? (
        <p className="mt-3 text-secondary text-text-muted">{finding.reason}</p>
      ) : null}

      {finding.evidence && finding.evidence.length > 0 ? (
        <div className="mt-3">
          <p className="text-label uppercase text-text-muted">Evidence</p>
          <ul className="mt-2 space-y-2">
            {finding.evidence.map((span) => (
              <li key={span.span_id} className="overflow-hidden rounded-card border border-border">
                <div className="flex items-center gap-2 border-b border-border bg-surface-alt px-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-label font-medium text-text">
                    {span.source_title ?? "Retrieved source"}
                  </span>
                  <span className="tabular shrink-0 text-label text-text-muted">
                    {span.text.length} characters
                  </span>
                </div>
                <p className="px-3 pt-2 text-secondary text-text-muted">{span.text}</p>
                <div className="px-3 pb-3 pt-2">
                  {span.source_url ? (
                    <SourceLink href={span.source_url}>View the source record</SourceLink>
                  ) : (
                    <span className="text-label text-text-muted">
                      Snapshotted at review time; no public record link.
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {suggestions.length > 0 ? (
        <div className="mt-3">
          <p className="text-label uppercase text-text-muted">
            {plural(suggestions.length, "suggested work")}
          </p>
          <ul className="mt-2 space-y-2">
            {suggestions.map((suggestion) => (
              <li key={suggestion.source_record_id}>
                <Suggestion suggestion={suggestion} />
              </li>
            ))}
          </ul>
          <p className="mt-2 text-label text-text-muted">
            Each of these came back from a provider search and is not in your bibliography. The
            relevance note is the identified model&rsquo;s reading; the record itself is the
            scholarly provider&rsquo;s.
          </p>
        </div>
      ) : null}

      {compact ? null : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {onView && finding.claim.paragraph_id ? (
            <Button variant="quiet" onClick={() => onView(finding)}>
              View in manuscript
            </Button>
          ) : null}

          {onHandle ? (
            <Button variant="quiet" onClick={() => onHandle(finding, !finding.handled)}>
              {finding.handled ? "Reopen" : "Mark handled"}
            </Button>
          ) : null}

          <details className="text-label text-text-muted">
            <summary className="inline-flex min-h-[24px] cursor-pointer items-center">
              Technical details
            </summary>
            <dl className="mt-2 space-y-1">
              <Detail term="Finding" value={finding.id} />
              {finding.claim.paragraph_id ? (
                <Detail term="Paragraph" value={finding.claim.paragraph_id} />
              ) : null}
              {finding.occurrence_id ? (
                <Detail term="Occurrence" value={finding.occurrence_id} />
              ) : null}
              {finding.reference_id ? (
                <Detail term="Reference" value={finding.reference_id} />
              ) : null}
              {finding.model ? (
                <Detail
                  term="Model"
                  value={`${finding.model_provider ?? "configured provider"} / ${finding.model}${
                    finding.prompt_version ? ` · ${finding.prompt_version}` : ""
                  }`}
                />
              ) : null}
            </dl>
          </details>
        </div>
      )}
    </Card>
  );
}

function Detail({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0">{term}</dt>
      <dd>
        <Mono>{value}</Mono>
      </dd>
    </div>
  );
}

function Suggestion({ suggestion }: { suggestion: SuggestedWork }) {
  const byline = [
    suggestion.authors?.slice(0, 3).join(", "),
    suggestion.year?.toString(),
    suggestion.venue,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="rounded-card border border-border p-3">
      <p className="text-secondary text-text">{suggestion.title}</p>
      {byline ? <p className="mt-1 text-label text-text-muted">{byline}</p> : null}
      {suggestion.rationale ? (
        <p className="mt-2 text-label text-text-muted">{suggestion.rationale}</p>
      ) : null}
      <p className="mt-2 flex flex-wrap items-center gap-2 text-label text-text-muted">
        <StatusLabel tone="neutral">{humanise(suggestion.provider)}</StatusLabel>
        {suggestion.url ? <SourceLink href={suggestion.url}>Open the record</SourceLink> : null}
        {suggestion.doi ? <Mono>{suggestion.doi}</Mono> : null}
      </p>
    </div>
  );
}

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  if (!href.startsWith("https://")) return <span className="text-text-muted">{href}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline underline-offset-2"
    >
      {children}
    </a>
  );
}
