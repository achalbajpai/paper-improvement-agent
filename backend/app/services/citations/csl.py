from __future__ import annotations

import re
from typing import Any

from lxml import etree

from app.domain.reference import (
    RAW_TEXT_KEY,
    CSLItem,
    NormalizationStatus,
)
from app.services.parser.xml_safety import NS, text_of

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

_YEAR = re.compile(r"(1[6-9]\d{2}|20\d{2})")
_ARXIV = re.compile(r"(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)


def biblstruct_xml_id(entry: etree._Element) -> str | None:
    value = entry.get(XML_ID)
    return str(value) if value else None


def raw_reference_text(entry: etree._Element) -> str:
    note = entry.find(".//tei:note[@type='raw_reference']", namespaces=NS)
    return text_of(note) if note is not None else ""


def to_csl(entry: etree._Element, reference_id: str) -> tuple[CSLItem, NormalizationStatus]:
    raw = raw_reference_text(entry)

    analytic = entry.find("tei:analytic", namespaces=NS)
    monogr = entry.find("tei:monogr", namespaces=NS)

    title = _first_text(analytic, "tei:title[@level='a']") if analytic is not None else ""
    container = ""
    if monogr is not None:
        journal = _first_text(monogr, "tei:title[@level='j']")
        book = _first_text(monogr, "tei:title[@level='m']")
        if title:
            container = journal or book
        else:
            title = journal or book

    authors = _authors(analytic) or _authors(monogr)
    year = _year(entry)
    identifiers = _identifiers(entry)

    fields: dict[str, Any] = {
        "id": reference_id,
        "type": _csl_type(entry, container=container, has_analytic=analytic is not None),
    }
    if title:
        fields["title"] = title
    if container:
        fields["container-title"] = container
    if authors:
        fields["author"] = authors
    if year is not None:
        fields["issued"] = {"date-parts": [[year]]}
    if identifiers.get("DOI"):
        fields["DOI"] = identifiers["DOI"]
    if identifiers.get("URL"):
        fields["URL"] = identifiers["URL"]

    fields.update(_imprint_fields(monogr))

    custom: dict[str, Any] = {}
    if raw:
        custom[RAW_TEXT_KEY] = raw
    if identifiers.get("arXiv"):
        custom["arxiv_id"] = identifiers["arXiv"]
    if custom:
        fields["custom"] = custom

    item = CSLItem.model_validate(fields)
    return item, _status(title=title, authors=authors, year=year)


def _imprint_fields(monogr: etree._Element | None) -> dict[str, Any]:
    if monogr is None:
        return {}
    candidates = {
        "volume": _first_text(monogr, ".//tei:biblScope[@unit='volume']"),
        "issue": _first_text(monogr, ".//tei:biblScope[@unit='issue']"),
        "publisher": _first_text(monogr, ".//tei:publisher"),
        "page": _pages(monogr),
    }
    return {key: value for key, value in candidates.items() if value}


def _status(*, title: str, authors: list[dict[str, Any]], year: int | None) -> NormalizationStatus:
    if title and authors and year is not None:
        return NormalizationStatus.COMPLETE
    if title or authors:
        return NormalizationStatus.PARTIAL
    return NormalizationStatus.RAW_ONLY


def _first_text(parent: etree._Element | None, path: str) -> str:
    if parent is None:
        return ""
    node = parent.find(path, namespaces=NS)
    return text_of(node) if node is not None else ""


def _authors(parent: etree._Element | None) -> list[dict[str, Any]]:
    if parent is None:
        return []
    authors: list[dict[str, Any]] = []
    for author in parent.findall("tei:author", namespaces=NS):
        organisation = author.find("tei:orgName", namespaces=NS)
        person = author.find("tei:persName", namespaces=NS)
        if person is not None:
            surname = _first_text(person, "tei:surname")
            forenames = [text_of(node) for node in person.findall("tei:forename", namespaces=NS)]
            given = " ".join(part for part in forenames if part)
            if surname or given:
                entry: dict[str, Any] = {}
                if surname and given:
                    entry["family"] = surname
                    entry["given"] = given
                else:
                    entry["literal"] = surname or given
                authors.append(entry)
        elif organisation is not None and text_of(organisation):
            authors.append({"literal": text_of(organisation)})
    return authors


def _year(entry: etree._Element) -> int | None:
    for date in entry.findall(".//tei:date", namespaces=NS):
        when = date.get("when") or text_of(date)
        match = _YEAR.search(str(when))
        if match:
            return int(match.group(1))
    return None


def _identifiers(entry: etree._Element) -> dict[str, str]:
    found: dict[str, str] = {}
    for idno in entry.findall(".//tei:idno", namespaces=NS):
        kind = (idno.get("type") or "").upper()
        value = text_of(idno)
        if not value:
            continue
        if kind == "DOI":
            found["DOI"] = value.replace("https://doi.org/", "").strip()
        elif kind == "ARXIV":
            match = _ARXIV.search(value)
            found["arXiv"] = match.group(1) if match else value
        elif kind in {"URL", "HTTP"} or value.startswith("http"):
            found["URL"] = value
    for ptr in entry.findall(".//tei:ptr[@target]", namespaces=NS):
        target = str(ptr.get("target") or "")
        if target.startswith("http") and "URL" not in found:
            found["URL"] = target
    return found


def _pages(monogr: etree._Element) -> str:
    scope = monogr.find(".//tei:biblScope[@unit='page']", namespaces=NS)
    if scope is None:
        return ""
    start = scope.get("from")
    end = scope.get("to")
    if start and end:
        return f"{start}-{end}"
    if start:
        return str(start)
    return text_of(scope)


def _csl_type(entry: etree._Element, *, container: str, has_analytic: bool) -> str:
    monogr = entry.find("tei:monogr", namespaces=NS)
    if monogr is not None:
        meeting = monogr.find(".//tei:meeting", namespaces=NS)
        if meeting is not None:
            return "paper-conference"
        journal = monogr.find("tei:title[@level='j']", namespaces=NS)
        if journal is not None and has_analytic:
            return "article-journal"
        if has_analytic and container:
            return "chapter"
        if monogr.find("tei:title[@level='m']", namespaces=NS) is not None and not has_analytic:
            return "book"
    if has_analytic:
        return "article-journal"

    return "document"
