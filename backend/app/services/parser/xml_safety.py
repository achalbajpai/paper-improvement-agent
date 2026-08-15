"""Hardened XML parsing.

TEI is parsed with entity resolution disabled, no network access, and a bounded
input size -- even though it comes from our own GROBID container. "It came from
our own service" is the assumption that ages badly: the container is reachable
on a local network, and a manuscript is attacker-influenced input travelling
through it.

Pure: constructs a parser and parses a string. No I/O of its own.
"""

from __future__ import annotations

from lxml import etree

from app.domain.errors import TeiMalformedError

MAX_XML_BYTES = 64 * 1024 * 1024

TEI_NS = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI_NS}


def safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )


def parse_tei(xml: str | bytes) -> etree._Element:
    payload = xml.encode("utf-8") if isinstance(xml, str) else xml
    if len(payload) > MAX_XML_BYTES:
        raise TeiMalformedError("TEI document exceeds the size limit.", bytes=len(payload))
    try:
        root = etree.fromstring(payload, parser=safe_parser())
    except etree.XMLSyntaxError as exc:
        raise TeiMalformedError("TEI is not well-formed XML.", detail=str(exc)) from exc
    if root is None:
        raise TeiMalformedError("TEI document is empty.")
    return root


def local_name(element: etree._Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def text_of(element: etree._Element) -> str:
    """All descendant text, whitespace-normalised.

    ``itertext`` is typed as yielding ``str | bytes`` because lxml can carry
    CDATA; TEI never does, and non-text nodes are skipped rather than coerced.
    """
    parts = [part for part in element.itertext() if isinstance(part, str)]
    return " ".join("".join(parts).split())
