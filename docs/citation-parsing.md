# Citation parsing

How a PDF becomes a manuscript this system is willing to edit and re-render, and
what it refuses to claim along the way.

The measured results are in [`evals/results/eval.md`](../evals/results/eval.md);
this document explains the design those numbers are measuring.

## The problem

A citation is not a piece of text. `[35, 2, 5]` is three separate references
printed as one marker; `(Peters et al., 2018a, p. 4)` is one reference plus a
locator plus a disambiguating letter; `(Dai and Le, 2015; Howard and Ruder,
2018)` is two, sharing one pair of brackets. If the system stores markers as
strings it can never re-render them in a different style, and it can never tell
the author which reference a sentence is leaning on.

So each marker becomes a `CitationNode` holding one or more `CitationItem`s, each
of which either points at a real bibliography entry or admits that it does not.

## Pipeline

```
PDF ──► GROBID ──► TEI ──► tei_mapper ──► postvalidator ──► Document + ReferenceRecords
                              │                │
                         XML-ID links     independent
                         clusters,        cross-check
                         modifiers
```

GROBID runs as `processFulltextDocument` with `includeRawCitations=1`,
`consolidateHeader=0`, `consolidateCitations=0` - consolidation off because it
replaces what the author wrote with what CrossRef thinks they meant, and this
system's whole posture is that the author's manuscript is the source of truth.

### tei_mapper

Everything here was written after reading the TEI this GROBID build actually
emits, not from the TEI specification. The checked-in TEI fixtures are captured
from the pinned GROBID version. Three observations shaped it:

**Clustering.** `[35, 2, 5]` arrives as three sibling `<ref>` elements whose text
carries the punctuation (`[35,`, `2,`, `5]`) and whose tails between them are
empty. Empty-tail adjacency is therefore the clustering signal — observed, not
guessed. Preserving the cluster gives the style renderer one grouped citation
occurrence rather than three unrelated occurrences.

**Absorbed openers.** In author-year papers the opening bracket and part of the
author list sit in the *preceding text*, not inside the ref: `(Dai and <ref>Le,
2015;</ref>`. Leaving that behind exports `(Dai and (Le, 2015)`. The mapper
absorbs the opener under tight guards.

**Linkage.** `target="#b12"` is an XML-ID and is resolved by lookup, never by
parsing `12` out of the string. `bibliography_order` is derived independently
from `listBibl` position. That independence is the entire reason the next stage
can check anything.

Markers whose internal structure cannot be determined get
`semantic_parse_status = RAW_ONLY`; markers whose page numbers or notes could not
be separated get `PARTIAL_MODIFIERS`. Both are `fidelity_exportable = False`, and
both are surfaced to the author before export rather than silently reformatted.

### postvalidator

The mapper linked a marker to a reference through GROBID's XML-ID. This stage
checks that link against a *different* signal: the number or author-and-year the
reader actually saw printed, compared against bibliography position derived from
`listBibl`. Neither quantity is computed from the other. A validator that
re-derives its expectation from the string it is checking always passes, and an
evaluation reporting its pass rate would measure nothing.

It does three things:

1. **Recovers** links GROBID missed. On `B_author_year`, GROBID leaves markers
   such as `(Peters et al., 2018a)` untargeted; matching surname and year against
   the bibliography linked 8 of them and promoted whole clusters that had been
   downgraded because one item of four failed to resolve. This is the single
   largest measured effect: `B_author_year` went from 56 structured occurrences
   to 64, and from 15 raw-only to 7.
2. **Downgrades** links the evidence contradicts, to `RAW_ONLY`. A citation
   pointing at the wrong paper is worse than one that admits it is only text.
3. **Reports** what it could not settle. On the corpus this left 2 uncertain and
   2 refused on `B_author_year`, and 2 refused on `C_numeric_dense` — numbers
   that appear in the UI as "left uncertain", not as silent success.

Numeric and author-year evidence are deliberately not weighted equally. A printed
`[13]` disagreeing with bibliography position 13 is decisive. A surname
disagreeing is not: GROBID mis-splits names - `Rie Kubota Ando` becomes family
`Kubota`, which the eval reports as a CSL error - so a surname mismatch is at
least as likely to be a metadata error as a linkage error. Downgrading on it
would destroy good citations to fix a bibliography field.

### Segmentation

There is exactly one segmenter and it is versioned (`seg/v1`). Claim extraction,
anchor creation, attachment re-verification, and citation insertion must all
agree on where sentences begin; two segmenters disagreeing by one boundary
produce anchors that silently point at the wrong sentence. The version is stored
on every anchor so a stored anchor is never compared across versions.

Citations are atomised to `[[CITE:cite_012]]` *before* segmentation. A marker
like `[1, p. 4.]` contains sentence-ending punctuation, so segmenting raw prose
would split a citation in half and attach the pieces to different sentences.
Replacing it with a token containing no boundary punctuation makes that
impossible rather than unlikely.

## Every reference gets a CSL item

`ReferenceRecord.csl` is never null. An entry GROBID could not parse becomes a
valid CSL item of `type: "document"` carrying the raw string in `custom` - not in
`note`, which several CSL styles render into the visible bibliography. The
alternative, dropping unparseable entries, means an export silently loses
bibliography lines, which is the worst failure this system could have.

`ReferenceRecord.csl.id == ReferenceRecord.id` is asserted at import, candidate
construction, verification, acceptance, and preflight. It is checked in five
places because when it is violated the symptom is a missing bibliography entry at
export time, with nothing in between to point at the cause.

This is the downstream contract: `Document`, `CitationNode`, `ReferenceRecord`,
CSL items, and `segmenter_version` are the canonical inputs to review, editing,
verification, and export. Unresolved citations remain visible and are surfaced
by export preflight; they are not silently dropped or rewritten as confident
structured citations.

## What the numbers do and do not say

Three papers cannot support a pass threshold, so the eval publishes no verdict.
Labels were read from the published PDFs, never from this parser's output.

Marker detection is essentially complete on the labelled sections: 20/20, 26/27,
36/36, with clusters preserved in every detected case. The single miss is
`(Peters et al., 2017, 2018a)` - a marker printed as one year list, which GROBID
emits mangled (`(peters et al., 2017(peters et al., , 2018a) )`) and which is
also the one false positive in the precision check.

Linkage on numeric papers is 20/20 and 36/36 against labels. `B_author_year` has
an unnumbered reference list, so there is no printed number to check against; its
linkage is assessed by the author-and-year corroboration instead, which is
weaker, and the report says so rather than reporting a number it cannot defend.

CSL field accuracy is the weakest area and is reported per field: titles 5/6 on
two papers (both misses are books whose titles GROBID left empty), first author
4/6 on `B_author_year` from the name-splitting problem above.

Preserved blocks are the honest ceiling. The semantic parser reports 2 of 5
figures recovered on `A_numeric`, and the "position not preserved" column is
nonzero everywhere. During source-asset attachment, coordinate-backed raster
figures are cropped from the uploaded PDF when available. GROBID still emits
floats detached from their anchor points, so an exported manuscript carries the
content but not the original page layout. A block with no recoverable source
asset remains an explicit export blocker rather than being dropped silently.
This is stated in the UI before an export runs, as a consequence the author has
to tick, rather than discovered afterwards.

## Fidelity ceiling

The export is a re-typeset manuscript, not a reproduction of the source PDF.
Structure, prose, citations, and the bibliography are preserved. Fonts, line
breaks, and float placement are not. Markers with `RAW_ONLY` or
`PARTIAL_MODIFIERS` status render as the author wrote them but are not
reformatted into the chosen style - the export preflight lists each one by ID.
