from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NOT_FOUND = "NOT_FOUND"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"

    OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    PROPOSAL_STATE_INVALID = "PROPOSAL_STATE_INVALID"
    STALE_REVISION = "STALE_REVISION"
    CANDIDATE_SNAPSHOT_MISMATCH = "CANDIDATE_SNAPSHOT_MISMATCH"
    VERIFICATION_MISMATCH = "VERIFICATION_MISMATCH"
    ACKNOWLEDGEMENT_REQUIRED = "ACKNOWLEDGEMENT_REQUIRED"
    OPERATION_TIMED_OUT = "OPERATION_TIMED_OUT"

    PARSER_UNAVAILABLE = "PARSER_UNAVAILABLE"
    PARSER_FAILED = "PARSER_FAILED"
    TEI_MALFORMED = "TEI_MALFORMED"

    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_BUDGET_EXHAUSTED = "PROVIDER_BUDGET_EXHAUSTED"
    NO_RESULTS = "NO_RESULTS"

    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LLM_REFUSED = "LLM_REFUSED"

    UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
    AMBIGUOUS_INTENT = "AMBIGUOUS_INTENT"
    GROUNDING_VALIDATION_FAILED = "GROUNDING_VALIDATION_FAILED"
    PROTECTED_TOKEN_VIOLATION = "PROTECTED_TOKEN_VIOLATION"
    UNSUPPORTED_NOVELTY = "UNSUPPORTED_NOVELTY"
    PROSE_MUTATION_FORBIDDEN = "PROSE_MUTATION_FORBIDDEN"
    IDENTITY_INVARIANT_VIOLATED = "IDENTITY_INVARIANT_VIOLATED"
    CANDIDATE_CONSTRUCTION_FAILED = "CANDIDATE_CONSTRUCTION_FAILED"
    VERIFICATION_BLOCKED = "VERIFICATION_BLOCKED"

    EXPORT_BLOCKED = "EXPORT_BLOCKED"
    UNKNOWN_CITATION_STYLE = "UNKNOWN_CITATION_STYLE"
    INVALID_ARTIFACT_NAME = "INVALID_ARTIFACT_NAME"
    EXPORT_NOT_READY = "EXPORT_NOT_READY"
    CITATION_RENDER_MISMATCH = "CITATION_RENDER_MISMATCH"
    RENDER_FAILED = "RENDER_FAILED"

    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            }
        }


class MalformedRequestError(AppError):
    code = ErrorCode.MALFORMED_REQUEST
    http_status = 400


class UnsupportedFileTypeError(AppError):
    code = ErrorCode.UNSUPPORTED_FILE_TYPE
    http_status = 415


class FileTooLargeError(AppError):
    code = ErrorCode.FILE_TOO_LARGE
    http_status = 413


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class IdempotencyKeyReusedError(AppError):
    code = ErrorCode.IDEMPOTENCY_KEY_REUSED
    http_status = 409


class OperationInProgressError(AppError):
    code = ErrorCode.OPERATION_IN_PROGRESS
    http_status = 409


class InvalidStateTransitionError(AppError):
    code = ErrorCode.INVALID_STATE_TRANSITION
    http_status = 409


class ProposalStateError(AppError):
    code = ErrorCode.PROPOSAL_STATE_INVALID
    http_status = 409


class StaleRevisionError(AppError):
    code = ErrorCode.STALE_REVISION
    http_status = 409


class CandidateSnapshotError(AppError):
    code = ErrorCode.CANDIDATE_SNAPSHOT_MISMATCH
    http_status = 409


class VerificationMismatchError(AppError):
    code = ErrorCode.VERIFICATION_MISMATCH
    http_status = 409


class AcknowledgementRequiredError(AppError):
    code = ErrorCode.ACKNOWLEDGEMENT_REQUIRED
    http_status = 409


class OperationTimedOutError(AppError):
    code = ErrorCode.OPERATION_TIMED_OUT
    http_status = 504


class ParserUnavailableError(AppError):
    code = ErrorCode.PARSER_UNAVAILABLE
    http_status = 503


class ParserFailedError(AppError):
    code = ErrorCode.PARSER_FAILED
    http_status = 422


class TeiMalformedError(AppError):
    code = ErrorCode.TEI_MALFORMED
    http_status = 422


class ProviderError(AppError):
    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = 502

    degrades_provider = True
    """Whether this failure says anything about the provider's health.

    A rate limit, an authentication failure or a server error will recur on the
    next call, so the operation stops using that provider. A rejection of one
    malformed query will not: the fault is ours, the next query is unaffected,
    and degrading would both lose the remaining coverage and report our own bug
    as somebody else's outage.
    """

    def __init__(self, message: str, provider: str = "", **details: Any) -> None:
        super().__init__(message, provider=provider, **details)
        self.provider = provider


class ProviderRateLimitedError(ProviderError):
    code = ErrorCode.PROVIDER_RATE_LIMITED
    http_status = 429


class ProviderUnavailableError(ProviderError):
    code = ErrorCode.PROVIDER_UNAVAILABLE


class ProviderNotFoundError(ProviderError):
    code = ErrorCode.PROVIDER_NOT_FOUND
    http_status = 404


class ProviderInvalidResponseError(ProviderError):
    code = ErrorCode.PROVIDER_INVALID_RESPONSE


class ProviderQueryRejectedError(ProviderError):
    code = ErrorCode.PROVIDER_INVALID_RESPONSE
    degrades_provider = False


class ProviderBudgetExhaustedError(ProviderError):
    code = ErrorCode.PROVIDER_BUDGET_EXHAUSTED
    http_status = 429


class NoResultsError(AppError):
    code = ErrorCode.NO_RESULTS
    http_status = 422


class LLMNotConfiguredError(AppError):
    code = ErrorCode.LLM_NOT_CONFIGURED
    http_status = 503


class LLMUnavailableError(AppError):
    code = ErrorCode.LLM_UNAVAILABLE
    http_status = 502


class LLMInvalidOutputError(AppError):
    code = ErrorCode.LLM_INVALID_OUTPUT
    http_status = 502


class LLMRateLimitedError(AppError):
    code = ErrorCode.PROVIDER_RATE_LIMITED
    http_status = 503


class LLMRefusedError(AppError):
    code = ErrorCode.LLM_REFUSED
    http_status = 422


class UnsupportedIntentError(AppError):
    code = ErrorCode.UNSUPPORTED_INTENT
    http_status = 422


class AmbiguousIntentError(AppError):
    code = ErrorCode.AMBIGUOUS_INTENT
    http_status = 422


class GroundingValidationError(AppError):
    code = ErrorCode.GROUNDING_VALIDATION_FAILED
    http_status = 502


class ProtectedTokenError(AppError):
    code = ErrorCode.PROTECTED_TOKEN_VIOLATION
    http_status = 422


class UnsupportedNoveltyError(AppError):
    code = ErrorCode.UNSUPPORTED_NOVELTY
    http_status = 422


class ProseMutationError(AppError):
    code = ErrorCode.PROSE_MUTATION_FORBIDDEN
    http_status = 422


class IdentityInvariantError(AppError):
    code = ErrorCode.IDENTITY_INVARIANT_VIOLATED
    http_status = 422


class CandidateConstructionError(AppError):
    code = ErrorCode.CANDIDATE_CONSTRUCTION_FAILED
    http_status = 422


class VerificationBlockedError(AppError):
    code = ErrorCode.VERIFICATION_BLOCKED
    http_status = 409


class ExportBlockedError(AppError):
    code = ErrorCode.EXPORT_BLOCKED
    http_status = 409


class UnknownCitationStyleError(AppError):
    code = ErrorCode.UNKNOWN_CITATION_STYLE
    http_status = 400


class InvalidArtifactNameError(AppError):
    code = ErrorCode.INVALID_ARTIFACT_NAME
    http_status = 400


class ExportNotReadyError(AppError):
    code = ErrorCode.EXPORT_NOT_READY
    http_status = 409


class CitationRenderMismatchError(AppError):
    code = ErrorCode.CITATION_RENDER_MISMATCH
    http_status = 500


class RenderFailedError(AppError):
    code = ErrorCode.RENDER_FAILED
    http_status = 500


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    http_status = 500


def as_app_error(error: BaseException) -> AppError:
    if isinstance(error, AppError):
        return error
    return InternalError("An unexpected error ended this operation.", cause=type(error).__name__)
