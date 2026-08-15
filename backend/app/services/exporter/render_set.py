"""What goes into the bibliography, and how an uncited entry is kept in it.

citeproc prints only the works a document actually cites. That is correct for a
document being written and wrong for a document being *edited*: shortening a
section can remove the last citation of a work the author chose, and letting the
bibliography quietly shrink would mean this system deleted source material as a
side effect of an edit nobody described that way.

So the reference set is derived from the AST, and every original entry that has
become uncited is retained through a **targeted** ``nocite`` -- naming those
entries specifically. The export contract verifies both halves: the targeted form
retains an entry, and the control without it confirms the entry is genuinely
absent otherwise, so the mechanism is doing the work rather than coinciding with
citeproc's default.

``nocite`` is targeted rather than ``@*`` because ``@*`` would also retain
references this system added and then dropped, which is a different decision the
researcher never made.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.document import Document
from app.domain.reference import ReferenceRecord


@dataclass(frozen=True)
class RenderSet:
    """The bibliography for one export, and the reason each entry is in it."""

    references: tuple[ReferenceRecord, ...]

    retained_uncited_ids: tuple[str, ...]

    def csl_json(self) -> list[dict[str, Any]]:
        """The bibliography as CSL-JSON, ready for ``--bibliography``.

        ``by_alias`` matters: CSL uses ``container-title``, and a field emitted as
        ``container_title`` is silently ignored by citeproc, which drops the
        journal name from every entry without erroring.
        """
        return [
            reference.csl.model_dump(mode="json", by_alias=True, exclude_none=True)
            for reference in self.references
        ]


def build(document: Document, *, original_reference_ids: frozenset[str]) -> RenderSet:
    """Decide the bibliography for this revision.

    ``original_reference_ids`` is the set from the *parsed* revision, not from the
    revision being exported. The distinction is the whole policy: an entry the
    author uploaded is retained when it falls out of use, and one this system
    added and then dropped is not, because retaining it would print a work nobody
    ever cited.
    """
    cited = document.cited_reference_ids()
    retained = tuple(
        reference.id
        for reference in document.references
        if reference.id not in cited and reference.id in original_reference_ids
    )
    return RenderSet(references=document.references, retained_uncited_ids=retained)


def dropped_added_reference_ids(
    document: Document, *, original_reference_ids: frozenset[str]
) -> tuple[str, ...]:
    """Added references that ended up cited nowhere.

    Not retained, and not silently left in the bibliography either: they are
    reported, because a bibliography entry for a work the manuscript never cites
    is a claim the researcher did not make.
    """
    cited = document.cited_reference_ids()
    return tuple(
        reference.id
        for reference in document.references
        if reference.id not in cited and reference.id not in original_reference_ids
    )
