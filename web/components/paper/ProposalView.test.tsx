import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ProposalView } from "./ProposalView";
import type { Proposal } from "@/lib/api/client";

afterEach(cleanup);

const noop = () => {};

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: "prop_1",
    paper_id: "paper_1",
    base_revision_id: "rev_1",
    command: "Shorten the introduction by about 20%",
    intent: "SHORTEN_SECTION",
    state: "AWAITING_DECISION",
    candidate_sha256: "abc",
    explanation: "Shortened Introduction by 126 words.",
    required_warning_ids: [],
    checks: [],
    blockers: [],
    warnings: [],
    diffs: [],
    delta: {
      changes: [],
      words_before: 6962,
      words_after: 6836,
      scope_words_before: 640,
      scope_words_after: 514,
      summary: "4 PARAGRAPH_TEXT_CHANGED. 640 -> 514 words in the 4 paragraphs it changed.",
    },
    scope: {
      section_id: "sec_1",
      section_title: "Introduction",
      paragraph_id: null,
      targeted_paragraph_ids: ["p_1_2", "p_1_3", "p_1_4", "p_1_5"],
      skipped: [{ paragraph_id: "p_1_1", reason: "only 31 words" }],
      words_before: 640,
      words_target: 512,
      section_paragraph_count: 6,
    },
    ...overrides,
  } as Proposal;
}

describe("the reported size of an edit", () => {
  it("counts the paragraphs it changed, not the manuscript around them", () => {
    render(<ProposalView proposal={proposal()} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText(/640 → 514 words/)).toBeTruthy();
    expect(screen.queryByText(/6962/)).toBeNull();
    expect(screen.queryByText(/6836/)).toBeNull();
  });

  it("names the part of the paper it acted on", () => {
    render(<ProposalView proposal={proposal()} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText("Introduction", { selector: "span" })).toBeTruthy();
    expect(screen.getByText(/Targeted 4 of 6 paragraphs in this section/)).toBeTruthy();
  });

  it("says a paragraph was left alone rather than staying silent about it", () => {
    render(<ProposalView proposal={proposal()} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText(/1 paragraph left alone/, { selector: "summary" })).toBeTruthy();
    expect(screen.getByText(/only 31 words/)).toBeTruthy();
  });

  it("reports a paragraph-scoped edit as one paragraph", () => {
    const narrow = proposal({
      scope: {
        section_id: "sec_1",
        section_title: "Introduction",
        paragraph_id: "p_1_3",
        targeted_paragraph_ids: ["p_1_3"],
        skipped: [],
        words_before: 142,
        words_target: 114,
        section_paragraph_count: 6,
      },
    });
    render(<ProposalView proposal={narrow} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText("One paragraph of Introduction")).toBeTruthy();
    expect(screen.getByText("This paragraph only.")).toBeTruthy();
  });

  it("does not claim a word change when only citations moved", () => {
    const cited = proposal({
      intent: "ADD_SUPPORTING_CITATIONS",
      delta: {
        changes: [],
        words_before: 6962,
        words_after: 6962,
        scope_words_before: 0,
        scope_words_after: 0,
        summary: "2 CITATION_ADDED. No prose changed.",
      },
    });
    render(<ProposalView proposal={cited} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText("No prose changed")).toBeTruthy();
  });
});

describe("a command that produced no edit", () => {
  it("shows the question the router raised, not only its error code", () => {
    const ambiguous = proposal({
      state: "FAILED",
      failure_code: "AMBIGUOUS_INTENT",
      failure_detail: "Did you mean the Introduction or Related Work?",
      delta: null,
      scope: null,
    });
    render(<ProposalView proposal={ambiguous} onAccepted={noop} onRejected={noop} />);

    expect(screen.getByText("Did you mean the Introduction or Related Work?")).toBeTruthy();
  });
});
