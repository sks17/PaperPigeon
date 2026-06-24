"""Parse ROR organization JSON into internal dataclasses  [Cursor task P1-T04].

Input = a SAVED ROR v2 organization response fixture (already-parsed dict). Output = a typed
dataclass with the institution identity the engine needs (ror id, canonical name, country,
aliases, relationships → parent/child orgs). PURE: no HTTP, no keys.

Define e.g. RorOrganization. Forbidden: editing openalex_parse.py, importing any HTTP client,
or touching anything outside this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RorRelationship:
    """Parent/child/related organization relationship from a saved ROR payload."""

    type: str
    id: str
    label: str | None


@dataclass(frozen=True)
class RorOrganization:
    """Institution identity fields used by the repopulation engine."""

    id: str
    name: str
    country: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    relationships: tuple[RorRelationship, ...] = field(default_factory=tuple)


def parse_ror_organization(payload: dict[str, Any]) -> RorOrganization:
    """Parse one saved ROR v2 organization object."""

    return RorOrganization(
        id=str(payload["id"]),
        name=_canonical_name(payload),
        country=_country(payload),
        aliases=tuple(_aliases(payload)),
        relationships=tuple(
            _parse_relationship(relationship)
            for relationship in payload.get("relationships", [])
            if isinstance(relationship, dict)
        ),
    )


def parse_ror_organizations(payload: dict[str, Any]) -> tuple[RorOrganization, ...]:
    """Parse either one organization object or a saved list response with ``items``."""

    if "items" in payload:
        return tuple(parse_ror_organization(item) for item in payload["items"])

    if "results" in payload:
        return tuple(parse_ror_organization(item) for item in payload["results"])

    return (parse_ror_organization(payload),)


def _canonical_name(payload: dict[str, Any]) -> str:
    names = payload.get("names", [])
    candidates = (
        [name for name in names if isinstance(name, dict)]
        if isinstance(names, list)
        else []
    )

    # Global priority pass: a `ror_display` entry anywhere wins over a `label` entry,
    # which wins over the first usable name. Returning on the first typed match (the old
    # behavior) let a localized label preceding the ror_display entry win incorrectly.
    for preferred_type in ("ror_display", "label"):
        for name in candidates:
            if preferred_type in (name.get("types") or []):
                value = _optional_str(name.get("value"))
                if value:
                    return value

    for name in candidates:
        value = _optional_str(name.get("value"))
        if value:
            return value

    return str(payload["name"])


def _aliases(payload: dict[str, Any]) -> list[str]:
    aliases: list[str] = []

    names = payload.get("names", [])
    if isinstance(names, list):
        for name in names:
            if not isinstance(name, dict):
                continue
            types = name.get("types", [])
            value = _optional_str(name.get("value"))
            if value and ("alias" in types or "acronym" in types):
                aliases.append(value)

    legacy_aliases = payload.get("aliases", [])
    if isinstance(legacy_aliases, list):
        aliases.extend(
            alias for alias in (_optional_str(value) for value in legacy_aliases) if alias
        )

    return aliases


def _country(payload: dict[str, Any]) -> str | None:
    locations = payload.get("locations", [])
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            geonames = location.get("geonames_details")
            if isinstance(geonames, dict):
                country = _optional_str(
                    geonames.get("country_name") or geonames.get("country_code")
                )
                if country:
                    return country

    country = payload.get("country")
    if isinstance(country, dict):
        return _optional_str(country.get("country_name") or country.get("country_code"))

    return _optional_str(country)


def _parse_relationship(payload: dict[str, Any]) -> RorRelationship:
    return RorRelationship(
        type=str(payload["type"]),
        id=str(payload["id"]),
        label=_optional_str(payload.get("label")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None
