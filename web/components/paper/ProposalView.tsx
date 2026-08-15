"use client";

import { useState } from "react";

import { DiffView } from "@/components/paper/DiffView";
import { Failed } from "@/components/States";
import { Mono } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { BlockingNotice, StatusLabel, type Tone } from "@/components/ui/StatusLabel";
import { api, type EditScope, type Proposal, type Schemas } from "@/lib/api/client";
import { CHECK_LABEL, ERROR_LABEL, humanise, plural } from "@/lib/labels";
import { useAction } from "@/lib/useAsync";

const CHECK_TONE: Record<Schemas["CheckStatus"], Tone> = {
  PASSED: "pass",
  WARNED: "warn",
  BLOCKED: "block",
  NOT_RUN: "neutral",
};

export function ProposalView({
  proposal,
  onAccepted,
  onRejected,
}: {
  proposal: Proposal;
  onAccepted: () => void;
  onRejected: () => void;
}) {
  const required = proposal.required_warning_ids ?? [];
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());
  const outstanding = required.filter((id) => !acknowledged.has(id));

  const accept = useAction(async (key: string) => {
    const result = await api.acceptProposal(
      proposal.id,
      proposal.candidate_sha256 ?? "",
      Array.from(acknowledged),
      key,
    );
    onAccepted();
    return result;
  }, proposal.id);
  const reject = useAction(async (key: string) => {
    const result = await api.rejectProposal(proposal.id, key);
    onRejected();
    return result;
  }, proposal.id);

  if (proposal.state === "FAILED") {
    return (
      <Card>
        <CardTitle>No edit from this command</CardTitle>
        <CardMeta className="mt-2">
          {ERROR_LABEL[proposal.failure_code ?? "INTERNAL_ERROR"] ??
            "We could not carry out this command."}
        </CardMeta>
        {proposal.failure_detail ? (
          <p className="mt-2 text-secondary">{proposal.failure_detail}</p>
        ) : null}
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-4">
        <CardTitle>{proposal.intent ? humanise(proposal.intent) : "Proposed edit"}</CardTitle>
        <StatusLabel tone={proposal.state === "BLOCKED" ? "block" : "neutral"}>
          {humanise(proposal.state)}
        </StatusLabel>
      </div>

      <CardMeta className="mt-1">&ldquo;{proposal.command}&rdquo;</CardMeta>

      {proposal.explanation ? <p className="mt-3 text-secondary">{proposal.explanation}</p> : null}

      {proposal.scope ? <Scope scope={proposal.scope} delta={proposal.delta ?? null} /> : null}

      {proposal.delta ? (
        <p className="mt-3 text-secondary text-text-muted">{proposal.delta.summary}</p>
      ) : null}

      <Checks checks={proposal.checks ?? []} />

      {(proposal.diffs ?? []).length > 0 ? (
        <div className="mt-4 space-y-3">
          {proposal.diffs?.map((diff) => <DiffView key={diff.paragraph_id} diff={diff} />)}
        </div>
      ) : null}

      {(proposal.blockers ?? []).length > 0 ? (
        <div className="mt-4">
          <BlockingNotice title="This edit will not be applied">
            <ul className="space-y-2">
              {proposal.blockers?.map((blocker, index) => (
                <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
              ))}
            </ul>
          </BlockingNotice>
        </div>
      ) : null}

      {required.length > 0 ? (
        <Acknowledgements
          warnings={(proposal.warnings ?? []).filter((warning) => required.includes(warning.id))}
          acknowledged={acknowledged}
          onToggle={(id) =>
            setAcknowledged((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
        />
      ) : null}

      {accept.failure ? <Failed className="mt-4" failure={accept.failure} /> : null}
      {reject.failure ? <Failed className="mt-4" failure={reject.failure} /> : null}

      {proposal.state === "AWAITING_DECISION" ? (
        <div className="mt-4 flex items-center gap-2">
          <Button
            variant="primary"
            disabled={accept.pending || outstanding.length > 0}
            onClick={() => void accept.run()}
          >
            {accept.pending ? "Applying…" : "Accept"}
          </Button>
          <Button variant="secondary" disabled={reject.pending} onClick={() => void reject.run()}>
            Discard
          </Button>
          {outstanding.length > 0 ? (
            <span className="text-secondary text-text-muted">
              {plural(outstanding.length, "consequence")} left to acknowledge.
            </span>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function Scope({ scope, delta }: { scope: EditScope; delta: Schemas["DeltaOut"] | null }) {
  const name = scope.section_title || scope.section_id;
  const where = scope.paragraph_id ? `One paragraph of ${name}` : name;
  const targeted = (scope.targeted_paragraph_ids ?? []).length;
  const skipped = scope.skipped ?? [];
  const changed = delta ? delta.scope_words_before !== delta.scope_words_after : false;

  return (
    <div className="mt-3 rounded-card border border-border bg-surface-alt p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-card-title">{where}</span>
        {changed && delta ? (
          <span className="tabular text-secondary text-text-muted">
            {delta.scope_words_before} → {delta.scope_words_after} words
          </span>
        ) : (
          <span className="text-secondary text-text-muted">No prose changed</span>
        )}
      </div>

      {targeted > 0 ? (
        <p className="mt-1 text-secondary text-text-muted">
          {scope.paragraph_id
            ? "This paragraph only."
            : `Targeted ${targeted} of ${plural(scope.section_paragraph_count, "paragraph")} in this section.`}
        </p>
      ) : null}

      {skipped.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-label text-text-muted">
            {plural(skipped.length, "paragraph")} left alone
          </summary>
          <ul className="mt-2 space-y-1">
            {skipped.map((item) => (
              <li key={item.paragraph_id} className="text-secondary text-text-muted">
                <Mono>{item.paragraph_id}</Mono> — {item.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Checks({ checks }: { checks: Schemas["CheckOut"][] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="mt-4 space-y-2">
      {checks.map((check, index) => (
        <li key={`${check.name}-${index}`} className="flex items-baseline gap-3">
          <StatusLabel tone={CHECK_TONE[check.status]} className="shrink-0">
            {humanise(check.status)}
          </StatusLabel>
          <span className="text-secondary">
            {CHECK_LABEL[check.name]}
            {check.detail ? <span className="text-text-muted"> — {check.detail}</span> : null}
            {check.model ? (
              <span className="text-text-muted">
                {" "}
                · {check.model_provider ?? "configured provider"} / <Mono>{check.model}</Mono>
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Acknowledgements({
  warnings,
  acknowledged,
  onToggle,
}: {
  warnings: Schemas["WarningOut"][];
  acknowledged: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="mt-4 rounded-card border border-warn/30 bg-warn-tint p-4">
      <p className="text-card-title text-warn">Accepting this means accepting:</p>
      <ul className="mt-3 space-y-3">
        {warnings.map((warning) => (
          <li key={warning.id}>
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 accent-accent"
                checked={acknowledged.has(warning.id)}
                onChange={() => onToggle(warning.id)}
              />
              <span className="text-secondary">
                {warning.message}
                {warning.subject_ids && warning.subject_ids.length > 0 ? (
                  <span className="ml-1 text-text-muted">
                    (<Mono>{warning.subject_ids.join(", ")}</Mono>)
                  </span>
                ) : null}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
