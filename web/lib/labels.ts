import type { Schemas } from "./api/client";

type Verdict = Schemas["SupportVerdict"];
type FindingKind = Schemas["FindingKind"];
type CheckName = Schemas["CheckName"];
type ErrorCode = Schemas["ErrorCode"];

export const VERDICT_LABEL: Record<Verdict, string> = {
  SUPPORTED: "Supported by the abstract",
  PARTIALLY_SUPPORTED: "Partly supported by the abstract",
  CONTRADICTED: "Contradicted by the abstract",
  UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE: "Not verifiable from the abstract",
  EVIDENCE_UNAVAILABLE: "No abstract available to check",
  SOURCE_IDENTITY_UNCERTAIN: "Could not confirm which work this is",
  SOURCE_UNRESOLVED: "This reference could not be matched to a known work",
};

export const VERDICT_NOTE = "Assessed against the abstract only. The full text may say more.";

export const FINDING_LABEL: Record<FindingKind, string> = {
  CITATION_SUPPORT: "Citation support",
  UNCITED_CLAIM: "Claim with no citation",
  MISSING_WORK: "Work you have not cited",
  UNRESOLVED_REFERENCE: "Unmatched reference",
  UNLINKED_CITATION: "Marker with no bibliography entry",
};

export const CHECK_LABEL: Record<CheckName, string> = {
  PROTECTED_TOKENS: "Citations left untouched",
  CITATION_PRESERVATION: "Citations preserved",
  SEMANTIC_ATTACHMENT: "Citations still fit their sentences",
  SEMANTIC_NOVELTY: "Nothing new asserted",
  NEW_CITATION_SUPPORT: "Added citations supported",
  STRUCTURE_PRESERVATION: "Structure preserved",
  BLOCK_PRESERVATION: "Figures and tables untouched",
  REFERENCE_COMPLETENESS: "New references complete",
  PROSE_IMMUTABILITY: "Wording unchanged",
};

export const ERROR_LABEL: Partial<Record<ErrorCode | "NETWORK_ERROR", string>> = {
  NETWORK_ERROR: "Could not reach the server.",
  NO_RESULTS: "We searched and found nothing worth citing here.",
  PROVIDER_RATE_LIMITED: "A search provider is rate-limiting us. Try again shortly.",
  PROVIDER_UNAVAILABLE: "A search provider is down.",
  PROVIDER_BUDGET_EXHAUSTED: "This run hit its search limit.",
  LLM_NOT_CONFIGURED: "No language model is configured, so this cannot run.",
  LLM_UNAVAILABLE: "The language model is down.",
  UNSUPPORTED_INTENT: "We can shorten a section or add supporting citations. Not this.",
  AMBIGUOUS_INTENT: "Tell us which part of the paper you mean.",
  STALE_REVISION: "Your paper changed since this was proposed. Run the command again.",
  CANDIDATE_SNAPSHOT_MISMATCH:
    "This changed since you opened it. Reload to see the current version.",
  ACKNOWLEDGEMENT_REQUIRED: "Tick the consequences before continuing.",
  OPERATION_IN_PROGRESS: "Something else is still running on this paper.",
  EXPORT_BLOCKED: "This cannot be exported cleanly.",
  PARSER_UNAVAILABLE: "The PDF parser is not reachable.",
};

export function humanise(value: string): string {
  return value
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^./, (character) => character.toUpperCase());
}

export function plural(count: number, singular: string, pluralForm?: string): string {
  return `${count} ${count === 1 ? singular : (pluralForm ?? `${singular}s`)}`;
}
