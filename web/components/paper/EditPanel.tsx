"use client";

import { useState } from "react";

import { ProposalView } from "@/components/paper/ProposalView";
import { Empty, Failed, Loading } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { CardMeta } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Input";
import { Working } from "@/components/ui/Working";
import { api, type EditTarget, type ManuscriptSection, type Proposal } from "@/lib/api/client";
import { ERROR_LABEL, humanise } from "@/lib/labels";
import { useAction, useAsync } from "@/lib/useAsync";

const ACTIONS = [
  { label: "Shorten this section", command: "Shorten the introduction by about 20%" },
  {
    label: "Add supporting citations",
    command: "Add supporting citations to the related work section",
  },
];

const REVIEWABLE = new Set<Proposal["state"]>(["PENDING", "AWAITING_DECISION", "BLOCKED"]);

export type Editing = ReturnType<typeof useEditing>;

export function useEditing(paperId: string, onAccepted: () => void) {
  const proposals = useAsync(() => api.listProposals(paperId), [paperId]);
  const [command, setCommand] = useState("");
  const [current, setCurrent] = useState<Proposal | null>(null);

  const propose = useAction(async (key: string, text: string, target?: EditTarget) => {
    const proposal = await api.createProposal(paperId, text, key, target);
    setCurrent(proposal);
    await proposals.reload();
    return proposal;
  }, command);

  const latest = current ?? proposals.data?.[0] ?? null;
  const shown = latest && REVIEWABLE.has(latest.state) ? latest : null;
  const refused = current?.state === "FAILED" ? current : null;

  return {
    proposals,
    command,
    setCommand,
    propose,
    shown,
    refused,
    history: (proposals.data ?? []).filter(
      (item) => item.id !== shown?.id && item.id !== refused?.id,
    ),
    accepted: () => {
      setCurrent(null);
      void proposals.reload();
      onAccepted();
    },
    rejected: () => {
      setCurrent(null);
      void proposals.reload();
    },
  };
}

export function EditComposer({
  editing,
  sections = [],
  available = true,
  onProposed,
}: {
  editing: Editing;
  sections?: ManuscriptSection[];
  available?: boolean;
  onProposed?: () => void;
}) {
  const { command, setCommand, propose, refused } = editing;

  const run = (text: string, target?: EditTarget) => {
    void propose.run(text, target).then((proposal) => {
      if (proposal && proposal.state !== "FAILED") onProposed?.();
    });
  };

  const ambiguous = refused?.failure_code === "AMBIGUOUS_INTENT";

  return (
    <section>
      <h2 className="text-section-title">Make an edit</h2>
      <CardMeta className="mt-1">
        Shorten a section, or add supporting citations. Nothing changes until you approve it.
      </CardMeta>

      {available ? null : (
        <p className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3 text-secondary text-warn">
          No language model is configured, so editing commands cannot run.
        </p>
      )}

      <div className="mt-4 flex flex-col gap-2">
        {ACTIONS.map((action) => (
          <Button
            key={action.label}
            variant="secondary"
            className="justify-start"
            disabled={!available || propose.pending}
            title={`Put "${action.command}" in the command box`}
            onClick={() => setCommand(action.command)}
          >
            {action.label}
          </Button>
        ))}
      </div>

      <label htmlFor="editing-command" className="mt-4 block text-label text-text-muted">
        Or write your own
      </label>
      <Textarea
        id="editing-command"
        className="mt-1"
        rows={3}
        value={command}
        placeholder="Shorten the introduction by about 20%"
        onChange={(event) => setCommand(event.target.value)}
      />

      <Button
        className="mt-2 w-full justify-center"
        variant="primary"
        disabled={!command.trim() || propose.pending || !available}
        onClick={() => run(command.trim())}
      >
        {propose.pending ? "Working…" : "Propose edit"}
      </Button>

      {propose.pending ? <Working className="mt-3" label="Drafting" /> : null}
      {propose.failure ? <Failed className="mt-3" failure={propose.failure} /> : null}

      {refused && !propose.pending ? (
        ambiguous ? (
          <WhichPart
            question={refused.failure_detail ?? null}
            sections={sections}
            onChoose={(sectionId) => run(refused.command, { sectionId })}
          />
        ) : (
          <div className="mt-3 rounded-card border border-border p-3">
            <p className="text-secondary">
              {ERROR_LABEL[refused.failure_code ?? "INTERNAL_ERROR"] ??
                "We could not carry out this command."}
            </p>
            {refused.failure_detail ? (
              <p className="mt-1 text-secondary text-text-muted">{refused.failure_detail}</p>
            ) : null}
          </div>
        )
      ) : null}
    </section>
  );
}

function WhichPart({
  question,
  sections,
  onChoose,
}: {
  question: string | null;
  sections: ManuscriptSection[];
  onChoose: (sectionId: string) => void;
}) {
  if (sections.length === 0) return null;
  return (
    <div className="mt-3 rounded-card border border-warn/30 bg-warn-tint p-3">
      <p className="text-label text-warn">Which part did you mean?</p>
      {question ? <p className="mt-1 text-secondary text-text-muted">{question}</p> : null}
      <div className="mt-3 flex flex-col gap-2">
        {sections.map((section) => (
          <Button
            key={section.id}
            variant="secondary"
            className="justify-start"
            onClick={() => onChoose(section.id)}
          >
            {section.title || "Untitled section"}
          </Button>
        ))}
      </div>
    </div>
  );
}

export function EditProposal({ editing }: { editing: Editing }) {
  const { proposals, shown, history } = editing;

  return (
    <section>
      <h2 className="text-section-title">Proposed edit</h2>

      {proposals.loading ? <Loading className="mt-3" /> : null}
      {proposals.failure ? (
        <Failed className="mt-3" failure={proposals.failure} onRetry={proposals.reload} />
      ) : null}

      {shown ? (
        <div className="mt-3">
          <ProposalView
            proposal={shown}
            onAccepted={editing.accepted}
            onRejected={editing.rejected}
          />
        </div>
      ) : (
        <Empty className="mt-3">No edits yet.</Empty>
      )}

      {history.length > 0 ? (
        <details className="mt-4">
          <summary className="cursor-pointer text-secondary text-text-muted">
            Earlier commands ({history.length})
          </summary>
          <ul className="mt-2 space-y-2">
            {history.map((item) => (
              <li
                key={item.id}
                className="flex items-baseline justify-between gap-4 rounded-card border border-border px-3 py-2"
              >
                <span className="text-secondary">{item.command}</span>
                <span className="shrink-0 text-label text-text-muted">{humanise(item.state)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
