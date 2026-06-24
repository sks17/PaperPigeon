"""Tests for ROR canonical-name selection (P2-T10).

`_canonical_name` (via `parse_ror_organization`) must prefer the ROR `ror_display` name, then a
`label`, then the first usable name — globally, not by first-typed-match. This fixes localized
names winning (e.g. "Universidad de Washington") when an English `ror_display` exists later in the
list. Pure: inline payloads, no network/DB/fixtures-file needed.
"""
from __future__ import annotations

from backend.repopulation.sources.ror_parse import parse_ror_organization


def _payload(names: list[dict]) -> dict:
    return {
        "id": "https://ror.org/01an7q238",
        "names": names,
        "locations": [{"geonames_details": {"country_name": "United States"}}],
    }


def test_canonical_name_prefers_ror_display_over_preceding_localized_labels() -> None:
    org = parse_ror_organization(
        _payload(
            [
                {"value": "Universidad de Washington", "types": ["label"], "lang": "es"},
                {"value": "Université de Washington", "types": ["label"], "lang": "fr"},
                {"value": "University of Washington", "types": ["ror_display", "label"], "lang": "en"},
                {"value": "UW", "types": ["acronym"], "lang": "en"},
            ]
        )
    )

    assert org.name == "University of Washington"


def test_canonical_name_falls_back_to_label_when_no_ror_display() -> None:
    org = parse_ror_organization(
        _payload(
            [
                {"value": "UW", "types": ["acronym"], "lang": "en"},
                {"value": "University of Washington", "types": ["label"], "lang": "en"},
            ]
        )
    )

    assert org.name == "University of Washington"


def test_canonical_name_falls_back_to_first_name_when_no_typed_match() -> None:
    org = parse_ror_organization(
        _payload(
            [
                {"value": "U Dub", "types": ["alias"], "lang": "en"},
                {"value": "UW", "types": ["acronym"], "lang": "en"},
            ]
        )
    )

    assert org.name == "U Dub"


def test_canonical_name_falls_back_to_legacy_name_field() -> None:
    org = parse_ror_organization(
        {"id": "https://ror.org/01an7q238", "name": "University of Washington"}
    )

    assert org.name == "University of Washington"
