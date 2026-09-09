"""Fixtures shaped like real Aleph API responses.

``tests/metadata.json`` is a verbatim capture of
https://search.openaleph.org/api/2/metadata, so the followthemoney model the
formatters are exercised against is the real one.
"""

import json
from pathlib import Path

import pytest

METADATA = json.loads((Path(__file__).parent / "metadata.json").read_text())


@pytest.fixture(scope="session")
def schemata():
    return METADATA["model"]["schemata"]


@pytest.fixture
def person():
    return {
        "id": "e1",
        "schema": "Person",
        "caption": "Ivan Petrov",
        "score": 14.2,
        "properties": {
            "name": ["Ivan Petrov", "Иван Петров"],
            "birthDate": ["1961-07-14"],
            "nationality": ["ru"],
            "position": ["Director"],
            "notes": ["x" * 400],
        },
        "collection": {
            "id": "c9",
            "label": "Russian company registry",
            "category": "company",
            "links": {"ui": "https://search.openaleph.org/datasets/c9"},
        },
        "links": {"ui": "https://search.openaleph.org/entities/e1"},
        "highlight": ["director <em>Ivan Petrov</em> was appointed"],
        "first_seen": "2019-03-01T00:00:00",
        "last_seen": "2024-11-02T00:00:00",
    }


@pytest.fixture
def company():
    return {
        "id": "e2",
        "schema": "Company",
        "caption": "Northwind Trading Ltd",
        "properties": {
            "name": ["Northwind Trading Ltd"],
            "jurisdiction": ["cy"],
            "registrationNumber": ["HE123456"],
        },
        "collection": {"id": "c9", "label": "Russian company registry"},
        "links": {"ui": "https://search.openaleph.org/entities/e2"},
    }


@pytest.fixture
def search_response(person, company):
    return {
        "status": "ok",
        "total": 57,
        "limit": 2,
        "offset": 0,
        "results": [person, company],
        "facets": {
            "countries": {
                "label": "Countries",
                "total": 12,
                "values": [
                    {"id": "ru", "label": "Russia", "count": 40},
                    {"id": "cy", "label": "Cyprus", "count": 9},
                ],
            },
            "schema": {"label": "Entity type", "values": []},
        },
    }
