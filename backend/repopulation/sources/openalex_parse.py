"""Parse OpenAlex author/works JSON into internal dataclasses  [Cursor task P1-T04].

Input = a SAVED OpenAlex response fixture (already-parsed dict). Output = typed dataclasses
suitable for building repop node_rows/edge_rows later. PURE: no HTTP, no keys, no rate-limit
logic — the main thread's OpenAlex client (which handles auth + the usage budget) passes
responses in.

Verified facts to honor (data-source-researcher, June 2026):
  - An OpenAlex author object carries the ORCID identifier inside it (ids.orcid) even though the
    ORCID API is dropped — extract it for dedup.
  - Author institution filter field is `last_known_institution`/`last_known_institutions`;
    works use `authorships[].institutions[]`. Topics ride inside the objects (no extra calls).
  - Abstracts arrive as `abstract_inverted_index`; reconstruct to plaintext if needed (data is
    CC0, so storing reconstructed text is permitted).

Define small @dataclass types (e.g. OpenAlexAuthor, OpenAlexWork) with the fields the engine
needs (id, orcid, display_name, last_known_institution id/ror, topics, recent works, counts).
Forbidden: editing ror_parse.py, importing requests/httpx/urllib, or touching anything outside
this file (fixtures may be added under ../tests/fixtures/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OpenAlexInstitution:
    """Institution identity embedded in OpenAlex author/works payloads."""

    id: str | None
    ror: str | None
    display_name: str | None


@dataclass(frozen=True)
class OpenAlexTopic:
    """Topic metadata carried by OpenAlex author and work objects."""

    id: str | None
    display_name: str
    score: float | None = None
    subfield: str | None = None
    field: str | None = None
    domain: str | None = None


@dataclass(frozen=True)
class OpenAlexWork:
    """Saved OpenAlex work fields needed for later graph enrichment."""

    id: str
    title: str | None
    publication_year: int | None
    doi: str | None
    cited_by_count: int | None
    abstract: str | None
    topics: tuple[OpenAlexTopic, ...] = field(default_factory=tuple)
    institutions: tuple[OpenAlexInstitution, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpenAlexAuthor:
    """Saved OpenAlex author fields needed for researcher dedup and enrichment."""

    id: str
    orcid: str | None
    display_name: str
    last_known_institution: OpenAlexInstitution | None
    topics: tuple[OpenAlexTopic, ...]
    recent_works: tuple[OpenAlexWork, ...]
    works_count: int | None
    cited_by_count: int | None
    h_index: int | None
    i10_index: int | None


def parse_openalex_author(payload: dict[str, Any]) -> OpenAlexAuthor:
    """Parse one saved OpenAlex author object."""

    return OpenAlexAuthor(
        id=str(payload["id"]),
        orcid=_optional_str(_nested(payload, "ids", "orcid")),
        display_name=str(payload["display_name"]),
        last_known_institution=_parse_institution(
            payload.get("last_known_institution")
            or _first(payload.get("last_known_institutions"))
        ),
        topics=tuple(_parse_topic(topic) for topic in payload.get("topics", [])),
        recent_works=tuple(
            parse_openalex_work(work) for work in _recent_work_payloads(payload)
        ),
        works_count=_optional_int(payload.get("works_count")),
        cited_by_count=_optional_int(payload.get("cited_by_count")),
        h_index=_optional_int(_nested(payload, "summary_stats", "h_index")),
        i10_index=_optional_int(_nested(payload, "summary_stats", "i10_index")),
    )


def parse_openalex_authors(payload: dict[str, Any]) -> tuple[OpenAlexAuthor, ...]:
    """Parse either one author object or a saved list response with ``results``."""

    if "results" in payload:
        return tuple(parse_openalex_author(author) for author in payload["results"])

    return (parse_openalex_author(payload),)


def parse_openalex_work(payload: dict[str, Any]) -> OpenAlexWork:
    """Parse one saved OpenAlex work object, including inverted-index abstracts."""

    return OpenAlexWork(
        id=str(payload["id"]),
        title=_optional_str(payload.get("title") or payload.get("display_name")),
        publication_year=_optional_int(payload.get("publication_year")),
        doi=_optional_str(payload.get("doi")),
        cited_by_count=_optional_int(payload.get("cited_by_count")),
        abstract=_abstract_from_inverted_index(payload.get("abstract_inverted_index")),
        topics=tuple(_parse_topic(topic) for topic in payload.get("topics", [])),
        institutions=tuple(_work_institutions(payload)),
    )


def parse_openalex_works(payload: dict[str, Any]) -> tuple[OpenAlexWork, ...]:
    """Parse either one work object or a saved list response with ``results``."""

    if "results" in payload:
        return tuple(parse_openalex_work(work) for work in payload["results"])

    return (parse_openalex_work(payload),)


def _parse_institution(payload: Any) -> OpenAlexInstitution | None:
    if not isinstance(payload, dict):
        return None

    return OpenAlexInstitution(
        id=_optional_str(payload.get("id")),
        ror=_optional_str(payload.get("ror")),
        display_name=_optional_str(payload.get("display_name")),
    )


def _parse_topic(payload: dict[str, Any]) -> OpenAlexTopic:
    return OpenAlexTopic(
        id=_optional_str(payload.get("id")),
        display_name=str(payload.get("display_name") or payload.get("name") or ""),
        score=_optional_float(payload.get("score")),
        subfield=_topic_label(payload.get("subfield")),
        field=_topic_label(payload.get("field")),
        domain=_topic_label(payload.get("domain")),
    )


def _recent_work_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("recent_works", "works"):
        works = payload.get(key)
        if isinstance(works, list):
            return [work for work in works if isinstance(work, dict)]

    works_api_url = payload.get("works_api_url")
    if isinstance(works_api_url, dict) and isinstance(works_api_url.get("results"), list):
        return [work for work in works_api_url["results"] if isinstance(work, dict)]

    return []


def _work_institutions(payload: dict[str, Any]) -> list[OpenAlexInstitution]:
    institutions: list[OpenAlexInstitution] = []

    for authorship in payload.get("authorships", []):
        if not isinstance(authorship, dict):
            continue
        for institution in authorship.get("institutions", []):
            parsed = _parse_institution(institution)
            if parsed:
                institutions.append(parsed)

    return institutions


def _abstract_from_inverted_index(payload: Any) -> str | None:
    if not isinstance(payload, dict) or not payload:
        return None

    words_by_position: dict[int, str] = {}
    for word, positions in payload.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                words_by_position[position] = str(word)

    if not words_by_position:
        return None

    return " ".join(words_by_position[index] for index in sorted(words_by_position))


def _topic_label(payload: Any) -> str | None:
    if isinstance(payload, dict):
        return _optional_str(payload.get("display_name") or payload.get("name"))

    return _optional_str(payload)


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]

    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None

    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None

    return float(value)
