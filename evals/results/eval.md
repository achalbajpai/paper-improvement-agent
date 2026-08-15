# Parser evaluation

Generated 2026-08-15T14:00:00+05:30 (IST).

Labels were read from the published PDFs, never from this parser's own
output. There is no pass threshold: three papers cannot support one.

## Evaluation scope

This is a small, targeted regression evaluation, not a complete benchmark. Given
the time constraint, broader confidence was built through manual workflow testing,
repeated local runs, and standard parser and integration checks. A larger,
representative corpus with repeated runs and production-level metrics would be
the next step.

## Marker detection, clustering, and linkage against hand labels

| paper | labelled | detected (raw) | detected (post) | clusters right | linkage right / checkable |
|---|---|---|---|---|---|
| A_numeric | 20 | 20 | 20 | 20 | 20 / 20 |
| B_author_year | 27 | 26 | 26 | 26 | not measurable |
| C_numeric_dense | 36 | 36 | 36 | 36 | 36 / 36 |

Linkage is scored against labels only where the printed marker carries a
bibliography position. `B_author_year` has an unnumbered reference list, so
its linkage is assessed by the independent author-and-year corroboration in
the next table instead.

## What postvalidation changed

| paper | family | checked | agreed | mismatched | recovered | promoted | downgraded | uncertain | refused |
|---|---|---|---|---|---|---|---|---|---|
| A_numeric | NUMERIC | 58 | 58 | 0 | 0 | 0 | 0 | 0 | 0 |
| B_author_year | AUTHOR_YEAR | 74 | 74 | 0 | 8 | 8 | 0 | 2 | 2 |
| C_numeric_dense | NUMERIC | 155 | 155 | 0 | 0 | 0 | 0 | 0 | 2 |

## Occurrence status, before and after

| paper | STRUCTURED raw | STRUCTURED post | RAW_ONLY raw | RAW_ONLY post | PARTIAL_MODIFIERS | item coverage |
|---|---|---|---|---|---|---|
| A_numeric | 45 | 45 | 0 | 0 | 0 | 1.0 |
| B_author_year | 56 | 64 | 15 | 7 | 0 | 0.9231 |
| C_numeric_dense | 115 | 115 | 2 | 2 | 0 | 0.9873 |

## Bibliography retention

| paper | labelled entries | TEI listBibl | retained records |
|---|---|---|---|
| A_numeric | 40 | 40 | 40 |
| B_author_year | None | 55 | 55 |
| C_numeric_dense | 50 | 50 | 50 |

## CSL field accuracy on hand-transcribed entries

| paper | entries | title | year | first author |
|---|---|---|---|---|
| A_numeric | 6 | 0.8333 | 1.0 | 1.0 |
| B_author_year | 6 | 1.0 | 0.8333 | 0.6667 |
| C_numeric_dense | 6 | 0.8333 | 1.0 | 1.0 |

## Preserved blocks

| paper | figures labelled / recovered | tables labelled / recovered | unrenderable | position not preserved |
|---|---|---|---|---|
| A_numeric | 5 / 2 | 4 / 3 | 2 | 8 |
| B_author_year | 5 / 4 | 8 / 4 | 0 | 16 |
| C_numeric_dense | 7 / 6 | 14 / 9 | 0 | 20 |

## Precision inside exhaustively labelled sections

- **A_numeric** (Background, Encoder and Decoder Stacks, Introduction, Model Architecture): 20 occurrences parsed against 20 labelled, 0 not in the printed text [].
- **B_author_year** (Introduction, Unsupervised Feature-based Approaches): 27 occurrences parsed against 27 labelled, 1 not in the printed text ['(peters et al., 2017(peters et al., , 2018a) )'].
- **C_numeric_dense** (Related Work, Shortcut Connections.): 15 occurrences parsed against 15 labelled, 0 not in the printed text [].

## Errors worth naming

- **A_numeric** title: expected 'Layer normalization', parsed '' (Layer normalization)
- **B_author_year** first_author_family: expected 'Ando', parsed 'Kubota' (A framework for learning predictive structures from multiple)
- **B_author_year** first_author_family: expected 'Bowman', parsed 'Samuel' (A large annotated corpus for learning natural language infer)
- **B_author_year** year: expected 2013, parsed 2009 (Distributed representations of words and phrases and their c)
- **B_author_year** marker not detected: (Peters et al., 2017, 2018a)
- **C_numeric_dense** title: expected 'A Multigrid Tutorial', parsed '' (A Multigrid Tutorial)
