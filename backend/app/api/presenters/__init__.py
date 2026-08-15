"""Row and model to response body.

Kept out of the route handlers so that ``api/`` stays HTTP translation and the
shape of a response is defined once rather than at each endpoint that returns it.
One module per resource, mirroring the route modules that consume them.
"""

from __future__ import annotations

from app.api.presenters.exports import export_run, preflight
from app.api.presenters.manuscript import manuscript
from app.api.presenters.papers import paper_detail, paper_summary, parse_quality
from app.api.presenters.proposals import proposal
from app.api.presenters.reviews import finding, review_run
from app.api.presenters.shared import _error_code

__all__ = [
    "_error_code",
    "export_run",
    "finding",
    "manuscript",
    "paper_detail",
    "paper_summary",
    "parse_quality",
    "preflight",
    "proposal",
    "review_run",
]
