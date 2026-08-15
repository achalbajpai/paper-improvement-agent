import type { components } from "./schema";

export type Schemas = components["schemas"];

export type Paper = Schemas["PaperDetail"];
export type PaperSummary = Schemas["PaperSummary"];
export type ParseQuality = Schemas["ParseQuality"];
export type ReviewRun = Schemas["ReviewRunOut"];
export type Finding = Schemas["FindingOut"];
export type Degradation = Schemas["DegradationOut"];
export type Proposal = Schemas["ProposalOut"];
export type EditScope = Schemas["EditScopeOut"];
export type ParagraphDiff = Schemas["ParagraphDiff"];

export type EditTarget = { sectionId?: string; paragraphId?: string };
export type ExportPreflight = Schemas["ExportPreflight"];
export type ExportRun = Schemas["ExportRunOut"];
export type Manuscript = Schemas["ManuscriptOut"];
export type ManuscriptSection = Schemas["SectionOut"];
export type ManuscriptParagraph = Schemas["ParagraphOut"];
export type InlineRun = Schemas["InlineRun"];
export type Reference = Schemas["ReferenceOut"];
export type SuggestedWork = Schemas["SuggestedWork"];
export type CitationStyle = Schemas["CitationStyle"];
export type ErrorCode = Schemas["ErrorCode"];

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly code: ErrorCode | "NETWORK_ERROR";
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    code: ErrorCode | "NETWORK_ERROR",
    message: string,
    status: number,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

interface Envelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

async function request<T>(
  path: string,
  init: RequestInit & { idempotencyKey?: string } = {},
): Promise<T> {
  const { idempotencyKey, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (rest.body && !(rest.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...rest, headers });
  } catch (cause) {
    throw new ApiError("NETWORK_ERROR", "Could not reach the server. Is the API running?", 0, {
      cause: String(cause),
    });
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Envelope;
    throw new ApiError(
      (body.error?.code as ErrorCode) ?? "INTERNAL_ERROR",
      body.error?.message ?? `Request failed with status ${response.status}.`,
      response.status,
      body.error?.details ?? {},
    );
  }
  return (await response.json()) as T;
}

export interface Capabilities {
  status: string;
  database: boolean;
  grobid: boolean;
  pandoc: boolean;
  llm_configured: boolean;
  openalex_keyed: boolean;
  semantic_scholar_keyed: boolean;
}

const UNAVAILABLE: Capabilities = {
  status: "degraded",
  database: false,
  grobid: false,
  pandoc: false,
  llm_configured: false,
  openalex_keyed: false,
  semantic_scholar_keyed: false,
};

export const api = {
  ready: async (): Promise<Capabilities> => {
    let response: Response;
    try {
      response = await fetch(`${BASE}/ready`);
    } catch {
      return UNAVAILABLE;
    }
    const body = (await response.json().catch(() => ({}))) as Partial<Capabilities>;
    return { ...UNAVAILABLE, ...body };
  },

  listPapers: () => request<PaperSummary[]>("/papers"),

  uploadPaper: (file: File, key: string) => {
    const form = new FormData();
    form.append("file", file);
    return request<PaperSummary>("/papers", { method: "POST", body: form, idempotencyKey: key });
  },

  getPaper: (paperId: string) => request<Paper>(`/papers/${paperId}`),

  getManuscript: (paperId: string) => request<Manuscript>(`/papers/${paperId}/manuscript`),

  parsePaper: (paperId: string, key: string) =>
    request<Paper>(`/papers/${paperId}/parse`, { method: "POST", idempotencyKey: key }),

  setCitationStyle: (paperId: string, style: CitationStyle) =>
    request<Paper>(`/papers/${paperId}/citation-style`, {
      method: "PATCH",
      body: JSON.stringify({ citation_style: style }),
    }),

  createReview: (paperId: string, key: string) =>
    request<ReviewRun>(`/papers/${paperId}/reviews`, {
      method: "POST",
      body: JSON.stringify({}),
      idempotencyKey: key,
    }),

  listReviews: (paperId: string) => request<ReviewRun[]>(`/papers/${paperId}/reviews`),

  getReview: (runId: string) => request<ReviewRun>(`/reviews/${runId}`),

  handleFinding: (findingId: string, handled: boolean) =>
    request<Finding>(`/findings/${findingId}`, {
      method: "PATCH",
      body: JSON.stringify({ handled }),
    }),

  createProposal: (paperId: string, command: string, key: string, target?: EditTarget) =>
    request<Proposal>(`/papers/${paperId}/proposals`, {
      method: "POST",
      body: JSON.stringify({
        command,
        section_id: target?.sectionId ?? null,
        paragraph_id: target?.paragraphId ?? null,
      }),
      idempotencyKey: key,
    }),

  listProposals: (paperId: string) => request<Proposal[]>(`/papers/${paperId}/proposals`),

  getProposal: (proposalId: string) => request<Proposal>(`/proposals/${proposalId}`),

  acceptProposal: (
    proposalId: string,
    candidateSha256: string,
    acknowledgedWarningIds: string[],
    key: string,
  ) =>
    request<Schemas["AcceptProposalResponse"]>(`/proposals/${proposalId}/accept`, {
      method: "POST",
      body: JSON.stringify({
        candidate_sha256: candidateSha256,
        acknowledged_warning_ids: acknowledgedWarningIds,
      }),
      idempotencyKey: key,
    }),

  rejectProposal: (proposalId: string, key: string) =>
    request<Proposal>(`/proposals/${proposalId}/reject`, {
      method: "POST",
      body: JSON.stringify({}),
      idempotencyKey: key,
    }),

  exportPreflight: (paperId: string) =>
    request<ExportPreflight>(`/papers/${paperId}/export/preflight`),

  createExport: (paperId: string, acknowledgedWarningIds: string[], key: string) =>
    request<ExportRun>(`/papers/${paperId}/exports`, {
      method: "POST",
      body: JSON.stringify({ acknowledged_warning_ids: acknowledgedWarningIds }),
      idempotencyKey: key,
    }),

  getExport: (runId: string) => request<ExportRun>(`/exports/${runId}`),

  artifactUrl: (href: string) => `${BASE}${href}`,
};
