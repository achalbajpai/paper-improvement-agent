"use client";

import { useEffect, useState } from "react";

import { Empty, Failed, Loading } from "@/components/States";
import { Mono } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CardMeta } from "@/components/ui/Card";
import { FilterChips, type FilterOption } from "@/components/ui/FilterChips";
import { Working } from "@/components/ui/Working";
import {
  api,
  type Capabilities,
  type Degradation,
  type Finding,
  type ReviewRun,
} from "@/lib/api/client";
import { FINDING_LABEL, plural, VERDICT_NOTE } from "@/lib/labels";
import { useAction, useAsync } from "@/lib/useAsync";

import { FindingCard } from "./FindingCard";

export function ReviewPanel({
  paperId,
  currentRevisionId,
  onFindings,
  available = true,
  capabilities = null,
  onViewFinding,
  onReviewed,
}: {
  paperId: string;
  currentRevisionId: string | null;
  onFindings?: (findings: Finding[], stale: boolean) => void;
  available?: boolean;
  capabilities?: Capabilities | null;
  onViewFinding?: (finding: Finding) => void;
  onReviewed?: () => void;
}) {
  const runs = useAsync(() => api.listReviews(paperId), [paperId]);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  const start = useAction(async (key: string) => {
    const run = await api.createReview(paperId, key);
    setOpenRunId(run.id);
    await runs.reload();
    return run;
  });

  const latest = runs.data?.find((run) => run.id === openRunId) ?? runs.data?.[0] ?? null;
  const running = latest?.status === "PENDING";

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void runs.reload(), 3000);
    return () => clearInterval(timer);
  }, [running, runs]);

  const settled = latest?.status === "COMPLETED" ? latest.id : null;
  useEffect(() => {
    if (settled) onReviewed?.();
  }, [settled, onReviewed]);
  const stale = latest !== null && latest.revision_id !== currentRevisionId;

  useEffect(() => {
    if (!onFindings) return;
    onFindings(latest && !stale ? (latest.findings ?? []) : [], stale);
  }, [latest, stale, onFindings]);

  return (
    <section>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-baseline sm:justify-between">
        <div>
          <h2 className="text-section-title">Peer review</h2>
          <CardMeta className="mt-1">
            We check whether your citations support the claims they sit on, and search for work you
            have not cited. Every source comes from Semantic Scholar or OpenAlex, with a link.
          </CardMeta>
        </div>
        <Button
          variant="secondary"
          className="self-start sm:self-auto"
          disabled={start.pending || running || !available}
          onClick={() => void start.run()}
        >
          {start.pending || running ? "Reviewing…" : "Run review"}
        </Button>
      </div>

      {available ? null : (
        <p className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3 text-secondary text-warn">
          No language model is configured, so review cannot run. Anything below is from an earlier
          review.
        </p>
      )}

      {capabilities && (!capabilities.openalex_keyed || !capabilities.semantic_scholar_keyed) ? (
        <p className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3 text-secondary text-warn">
          Search coverage will be reduced.
          {!capabilities.openalex_keyed ? " OpenAlex has no API key." : ""}
          {!capabilities.semantic_scholar_keyed
            ? " Semantic Scholar has no API key, so it may rate-limit us."
            : ""}{" "}
          Any provider we cannot reach is named in the results.
        </p>
      ) : null}

      {start.pending || running ? <Working className="mt-4" label="Reviewing your paper" /> : null}

      {start.failure ? <Failed className="mt-3" failure={start.failure} /> : null}
      {runs.loading ? <Loading className="mt-3" /> : null}
      {runs.failure ? (
        <Failed className="mt-3" failure={runs.failure} onRetry={runs.reload} />
      ) : null}

      {runs.data && runs.data.length === 0 && !start.pending ? (
        <Empty className="mt-3">No review yet.</Empty>
      ) : null}

      {stale && latest ? (
        <div className="mt-4 rounded-card border border-warn/30 bg-warn-tint p-3">
          <p className="text-secondary text-warn">
            This review ran on an earlier revision, so it may point at wording you have since
            changed. Run it again for the current text.
          </p>
        </div>
      ) : null}

      {latest ? (
        <RunView
          run={latest}
          onViewFinding={onViewFinding}
          onHandle={(finding, handled) => {
            void api.handleFinding(finding.id, handled).then(() => runs.reload());
          }}
        />
      ) : null}
    </section>
  );
}

function Coverage({
  stats,
  findings,
  degradations,
}: {
  stats: Record<string, unknown>;
  findings: number;
  degradations: Degradation[];
}) {
  const count = (name: string): number => {
    const value = stats[name];
    return typeof value === "number" ? value : 0;
  };
  const paragraphs = count("paragraphs_total");
  const paragraphLimit = count("paragraph_limit");
  const references = count("references_total");
  const supportTotal = count("support_assertions_total");
  const supportCompleted = count("support_assertions_completed");

  return (
    <div className="mt-3 rounded-card border border-border p-3">
      <p className="text-secondary text-text">
        {plural(findings, "finding")}. Here is exactly what we covered.
      </p>
      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-label text-text-muted">
        <li>
          <Mono>
            {count("paragraphs_reviewed")}
            {paragraphs ? ` / ${paragraphs}` : ""}
          </Mono>{" "}
          paragraphs read
        </li>
        {paragraphLimit > 0 ? (
          <li>
            <Mono>{paragraphLimit}</Mono> paragraph limit
          </li>
        ) : null}
        <li>
          <Mono>
            {count("references_examined")}
            {references ? ` / ${references}` : ""}
          </Mono>{" "}
          references checked
        </li>
        <li>
          <Mono>{count("references_resolved")}</Mono> matched to a real work
        </li>
        <li>
          <Mono>{count("claims_searched")}</Mono> claims searched for missing work
        </li>
        <li>
          <Mono>{count("missing_work_paragraphs_searched")}</Mono> paragraphs searched for missing
          work
        </li>
        <li>
          <Mono>
            {supportCompleted}
            {supportTotal ? ` / ${supportTotal}` : ""}
          </Mono>{" "}
          citation claims assessed
        </li>
        <li>
          <Mono>{count("provider_calls")}</Mono> provider calls
        </li>
      </ul>
      {supportTotal > 0 && supportCompleted < supportTotal ? (
        <p className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3 text-secondary text-warn">
          We did not check every citation in this run. What we skipped is unknown, not clean.
        </p>
      ) : null}
      {degradations.length > 0 ? (
        <div className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3">
          <p className="text-secondary text-warn">
            We reached fewer sources than we should have. These are the results of a reduced search,
            not a complete one.
          </p>
          <ul className="mt-2 space-y-1">
            {degradations.map((item, index) => (
              <li key={`${item.provider}-${index}`} className="text-secondary text-text-muted">
                {item.provider}: <Mono>{item.code}</Mono> {item.detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="mt-2 text-label text-text-muted">{VERDICT_NOTE}</p>
    </div>
  );
}

const KINDS = [
  "CITATION_SUPPORT",
  "MISSING_WORK",
  "UNCITED_CLAIM",
  "UNRESOLVED_REFERENCE",
  "UNLINKED_CITATION",
] as const;

type KindFilter = "all" | "handled" | (typeof KINDS)[number];

export function kindOptions(findings: Finding[]): FilterOption<KindFilter>[] {
  const open = findings.filter((finding) => !finding.handled);
  const handled = findings.length - open.length;
  const present = KINDS.filter((kind) => open.some((finding) => finding.kind === kind));
  return [
    { id: "all", label: "Open", count: open.length },
    ...present.map((kind) => ({
      id: kind,
      label: FINDING_LABEL[kind],
      count: open.filter((finding) => finding.kind === kind).length,
    })),
    ...(handled > 0 ? [{ id: "handled" as const, label: "Handled", count: handled }] : []),
  ];
}

function RunView({
  run,
  onViewFinding,
  onHandle,
}: {
  run: ReviewRun;
  onViewFinding?: (finding: Finding) => void;
  onHandle?: (finding: Finding, handled: boolean) => void;
  onReviewed?: () => void;
}) {
  const [kind, setKind] = useState<KindFilter>("all");

  if (run.status === "FAILED") {
    return (
      <Failed
        className="mt-4"
        failure={{
          code: run.failure_code ?? "INTERNAL_ERROR",
          message: "This review did not finish.",
        }}
      />
    );
  }

  const findings = run.findings ?? [];
  const degradations = run.degradations ?? [];
  const shown =
    kind === "handled"
      ? findings.filter((finding) => finding.handled)
      : kind === "all"
        ? findings.filter((finding) => !finding.handled)
        : findings.filter((finding) => !finding.handled && finding.kind === kind);

  return (
    <div className="mt-4">
      <Coverage stats={run.stats ?? {}} findings={findings.length} degradations={degradations} />

      {findings.length > 0 ? (
        <FilterChips
          className="mt-4"
          label="Filter findings by kind"
          options={kindOptions(findings)}
          active={kind}
          onSelect={setKind}
        />
      ) : null}

      <ul className="mt-3 space-y-3">
        {shown.map((finding) => (
          <li key={finding.id}>
            <FindingCard finding={finding} onView={onViewFinding} onHandle={onHandle} />
          </li>
        ))}
      </ul>
    </div>
  );
}
