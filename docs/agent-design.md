# Agent design

How a natural-language command becomes an edit the author can trust, and what
stops it becoming one they cannot.

## The premise

A language model is good at proposing changes to prose and bad at being
accountable for them. So it is never the thing that applies a change, never the
thing that reports what it changed, and never the source of any text quoted back
to the author as evidence. It proposes; the server decides, applies, and
verifies.

Except for the shortening rewrite, model calls return **server-issued
identifiers or structured judgements**. Claim text comes from the segmenter.
Evidence text comes from a snapshotted abstract by character offset. Candidate
works come from provider HTTP responses. The rewrite is untrusted candidate
prose: its `claimed_actions` and rationale are not evidence, and the text passes
through citation-token checks, a novelty heuristic, and an independent diff
before anyone can accept it.

## Shape of a request

```
command ──► router ──► planner ──► worker ──► CandidateRevisionBuilder
                                                    │
                                      DeltaEngine (independent diff)
                                                    │
                                              verification
                                                    │
                                   blockers / warnings / pass
                                                    │
                                      author acknowledges + accepts
                                                    │
                                        fenced acceptance transaction
                                                    │
                                            immutable revision
```

Nothing between the command and acceptance touches the paper. A proposal is a
document sitting in a column that nothing reads until the author says so.

## Routing

The model classifies the command into `SHORTEN_SECTION`,
`ADD_SUPPORTING_CITATIONS`, `UNSUPPORTED_INTENT`, or `AMBIGUOUS_INTENT`. Every
identifier it returns is then checked against the actual document, so a plausible but
non-existent section id becomes a typed error rather than an edit applied
somewhere else.

Unsupported and ambiguous are outcomes, not failures to work around. "Translate
this paper into French" gets a refusal that says what the system can do. A
command that does not say which section it means returns a clarification, because
guessing means silently editing a part of the paper nobody asked about.

An ambiguous command returns a clarification and requires an explicit target.
The server does not guess a section or manufacture a list of possible targets;
the UI can ask the researcher to select one and re-run the command with that
server-validated id.

Where a command acts and what it asks for are resolved separately, because they
have different reliability. Intent and ratio are read from the prose. The target
is taken from the researcher's own selection when there is one, and only inferred
otherwise; a supplied target outranks the model's reading and is still checked
against the document, since the browser can be holding a manuscript a revision
behind. Scope is honoured at whatever grain the command used: naming a paragraph
edits that paragraph, not the twelve around it.

## SHORTEN_SECTION

Rewriting is scoped to one paragraph at a time. A section-wide rewrite gives the
model licence to reorganise, and reorganisation is indistinguishable from
deletion once you are looking at a diff.

Three defences, each catching what the others cannot:

**Protected tokens.** The rewrite is checked for preservation, removal,
duplication, and mutation of opaque citation tokens. Numbers, named entities,
and equations are not structurally protected by this module; the novelty check
and the human diff cover those risks imperfectly. This catches the common
failure — a shortening that quietly drops `[12]`.

**Semantic novelty.** Tokens cannot see an invented fact:

```
before  "The approach may reduce latency."
after   "The approach reduces latency by 30%."
```

Tokens intact, diff clean, manuscript may still be false. The novelty check is a
model-based heuristic that flags assertions not supported by the original text;
it is not a formal entailment proof. A paragraph whose rewrite fails the check is
left unchanged. If every target paragraph fails, the command fails; if only some
fail, the proposal carries a warning and shows the unchanged paragraphs.

For a model that does not pass the model-safety evaluation, deployment policy can
set `SHORTEN_EXTRACTIVE_ONLY=true`. That is an explicit configuration decision,
not an automatic runtime switch. Extractive mode avoids new assertions by
deleting whole sentences, but it is less expressive than a rewrite.

**Independent diff.** See DeltaEngine below.

Candidate construction and persistence are atomic. Individual paragraphs that
fail novelty remain unchanged and are reported; successful paragraph edits can
still appear in the candidate, so the author reviews exactly the resulting diff.

## ADD_SUPPORTING_CITATIONS

The intent that most obviously wants to hallucinate is deliberately constrained:
**no prose is regenerated**. A model chooses which retrieved work supports which
sentence; trusted code inserts one token and nothing else, and the paragraph is
proved unchanged by comparing it with the tokens stripped.

The path, and why each step is load-bearing:

| # | Step | Why |
|---|---|---|
| 1-2 | Locate the section, segment with the canonical segmenter | Sentence ids must mean the same thing here as in review |
| 3-4 | Ask which sentences make a citable claim; skip already-cited ones | Adding a second source to a supported claim is not what the command asked for |
| 5 | Build the query from the claim's *topic*, not its prose | The manuscript is not posted to a third party verbatim |
| 6-7 | Search both providers; no candidates raises `NO_RESULTS` | Becomes a FAILED proposal row, not a silently empty edit |
| 8-10 | Rerank by id; attach by id; validate every id against the allowlist | The candidate set is closed and server-issued |
| 11-12 | Snapshot the work; check support against the snapshotted abstract | A work that contradicts the sentence is dropped and never offered |
| 13 | Require complete CSL | A citation the reader cannot look up is a gesture at one |
| 14 | Mint `ref_added_NNN` with `csl.id == id` | The invariant that otherwise silently deletes bibliography entries at export |
| 15-17 | Deterministic marker position, build candidate, verify | Joins an adjacent occurrence rather than opening a second bracket |

`NO_RESULTS` is worth naming separately: retrieval finding nothing and retrieval
being *unable to look* are different outcomes with different codes. Semantic
Scholar may return 429 under a shared unauthenticated quota, so
`PROVIDER_RATE_LIMITED` is reported distinctly and surfaces in the UI rather than
being folded into "no citations found".

## DeltaEngine

The delta is computed by comparing the before and after documents. It never reads
the model's account of what it did, and never reads the editing code's account
either - both come from the same step, so neither can check the other.

The failure this exists to catch is ordinary: a model asked to shorten reports
"removed two sentences" and has also dropped a citation. The report is clean, the
mutation is not. Everything the UI shows and everything verification decides is
derived from the independent comparison, not from any self-report.

It reports two pairs of word counts, because they answer two different questions.
One covers the manuscript; the other covers only the paragraphs that changed. The
difference between before and after is identical in both, but the denominator is
not, and a section-scoped trim shown against the document total reads as though
the whole paper had been rewritten. The scoped pair is what the proposal displays.

## Verification

Every outcome is one of three things, and which one depends on **who is entitled
to decide**, not on how bad it sounds.

**Pass** - the check found nothing.

**Warning** - a real consequence the author may accept, named specifically enough
to be worth acknowledging. "Citation [12] will no longer appear in section 3" is
a warning. "This edit may have risks" is not an acknowledgement of anything, and
collecting a checkbox against it is theatre.

**Blocker** - something the system will not do whatever the author says, because
agreeing would require already knowing the thing the check exists to establish.

Two rules that look like details and are not:

`after_verdict == CONTRADICTED` blocks regardless of the verdict before the edit.
Blocking only on a *transition* would let an already-contradicted citation
survive a rewrite untouched - precisely the citation most needing attention.

Warning identity is derived from content and bound to the candidate's hash, so an
acknowledgement collected for one proposal cannot be replayed against a
regenerated one that removes a different citation.

Semantic attachment is re-checked when a citation's **context changed**, not when
it merely moved. Movement without changed surrounding prose does not alter what
the citation is claiming.

## Acceptance

The one operation that moves the paper, so the one that has to be right under
concurrency.

The transaction takes the **paper lock first, then the proposal lock**, always in
that order. Acceptance and rejection use this order; startup recovery is a
separate reconciliation transaction and does not try to acquire this lock pair.

Re-checking at acceptance is not paranoia about our own code. The author read a
candidate, thought about it, and clicked accept some minutes later; in between the
paper may have moved. So the base revision is re-compared, the snapshot hash
re-derived, and acknowledgements re-matched against the warnings required *now*.

The stored revision is the snapshot's document. **The edit is never re-run.** If
it were, the author's decision and the stored result could differ, which is the
entire point a candidate revision exists to prevent.

`UNIQUE(accepted_proposal_id)` on `document_revisions` enforces once-only
application at the database, and `test_concurrency.py` asserts that constraint
holds with application-level advisory locking disabled - a lock that is the only
thing standing between two requests and a double-apply is a lock you cannot
deploy behind more than one process.

## Grounded review

The review answers two questions in two passes over the manuscript. Separate
model calls with separate schemas and separate fixtures throughout - never one
prompt doing several jobs, because a prompt doing several jobs cannot be
evaluated on any of them.

**Does the work you cited support what you said?** Per cited claim, from the
cited work's abstract.

**What did you not cite that you should have?** Per claim, by searching both
providers for work absent from the bibliography.

The current review worker is deliberately bounded. `start_review` persists a
pending run and executes it in a daemon thread inside the API process; it is not a
durable queue. A process restart stops the work, and startup reconciliation marks
the abandoned run failed rather than resuming it. The support pass is capped by
`max_review_paragraphs` and missing-work search by `max_missing_work_claims` (the
current defaults are 500 and 12). Run statistics report the total paragraphs,
paragraphs reviewed, claims searched, provider calls, and degradations so a
partial review is visible rather than presented as full-paper coverage.

### Missing work

The half where a system can do real damage, because a recommendation asserts
that a work exists, is real, and is relevant. Three properties keep it honest.

**Every suggestion is a snapshotted provider record.** A suggestion is a
`source_records` row written from a real OpenAlex or Semantic Scholar response.
The model ranks ids drawn from that set and never names a work, so a fabricated
recommendation is not something this path can express - the same closed-set
grounding that `ADD_SUPPORTING_CITATIONS` uses, for the same reason.

**Suggestions are deduplicated against the manuscript's own bibliography.**
Telling a researcher to cite a paper they already cite is worse than saying
nothing: it is confidently wrong about the document in front of it. Matching is
on DOI, arXiv id, provider identity, and title similarity, because a
bibliography entry and a provider record rarely agree on all four - on the
corpus papers GROBID often recovers a title and no identifier at all. The index
that answers this is shared with the citation adder rather than reimplemented,
so the two cannot drift into disagreeing about what the paper cites.

**"We found nothing" and "we could not look" are different answers.** A claim
searched with nothing usable back is reported as searched. A claim whose search
met a degraded provider names the providers that actually answered, rather than
claiming both were consulted.

That distinction earns its keep. Provider failures are recorded as degradation
for the operation and remain distinct from a successful search that returned no
usable work.

Claim selection for this pass is deliberately *not* the support pass's
density-first ordering. Missing work is likeliest exactly where citations are
sparse, so ranking paragraphs by how many citations they already carry would
search hardest where there is least to find. Sections are ordered by where an
uncited claim does damage - introductions and related work first, methods and
results last - and ties break on document order, so two runs over one manuscript
search the same paragraphs.

### Citation support

**Per (claim, occurrence, reference).** `[2, 5]` is two separate assertions of
support. One verdict about the sentence would hide which of the two is weak,
which is the only actionable part.

**Source state is never a model's judgement.** Unresolved, uncertain, and
abstract-unavailable are decided by the server from the resolution ladder, and
the model is not asked about a work whose identity is not established - a verdict
against the wrong paper is worse than no verdict. An `UNCERTAIN` resolution
yields `SOURCE_IDENTITY_UNCERTAIN`, never a support verdict.

**Absence of evidence is not a finding against the author.** There is no
`UNSUPPORTED` verdict. The strongest negative is `CONTRADICTED`, which is a claim
about what the abstract *says*, not about what the cited paper contains. A model
that has not seen the full text is not entitled to the stronger claim. This is an
intentional limitation: the brief asks for support review of existing citations,
but this implementation reports abstract-level uncertainty instead of claiming
that a full cited paper is unsupported.

Unknown ids from any call raise a typed `GroundingValidationError` - never
`assert`, which `python -O` strips out of exactly the deployment where it matters.

## Failure is a state, not an absence

A failed review still writes a run row: "the review errored" and "the review found
nothing" are different, and a run that vanished on failure leaves the author
unable to tell which happened. Empty retrieval still creates the proposal row,
left `FAILED` with no candidate snapshot. Provider degradation is attached to the
result and shown, not logged and forgotten.
