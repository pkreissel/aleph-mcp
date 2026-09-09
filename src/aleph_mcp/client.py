"""Read-only Aleph API access, layered on the official ``alephclient`` library.

``alephclient.api.AlephAPI`` handles host/API-key configuration, the requests
session and the auth header. It wraps a subset of the API, so the endpoints it
does not cover (search with facets, expand, similar, xref, statistics,
metadata) are issued here as raw GETs over the same configured session.

Only GET requests are ever made: this client cannot mutate Aleph data.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

from alephclient.api import AlephAPI
from alephclient.errors import AlephException
from alephclient.util import backoff
from requests import RequestException
from requests.exceptions import HTTPError

log = logging.getLogger(__name__)

# Aleph rejects very large page sizes; keep requests within a sane band.
MAX_LIMIT = 200


class AlephReadClient:
    """A read-only view of one Aleph instance."""

    def __init__(
        self,
        host: str | None = None,
        api_key: str | None = None,
        retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        # AlephAPI falls back to ALEPH_HOST / ALEPH_API_KEY when args are None.
        self.api = AlephAPI(host=host, api_key=api_key)
        self.retries = max(1, retries)
        self.timeout = timeout
        self._metadata: dict[str, Any] | None = None

    @property
    def base_url(self) -> str:
        return self.api.base_url

    @property
    def host(self) -> str:
        """Instance root, e.g. ``https://search.openaleph.org/``."""
        return urljoin(self.base_url, "/")

    @property
    def authenticated(self) -> bool:
        return "Authorization" in self.api.session.headers

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` relative to ``/api/2/``, retrying transient failures."""
        url = urljoin(self.base_url, path.lstrip("/"))
        cleaned = _clean_params(params or {})
        failures = 0
        while True:
            try:
                response = self.api.session.get(
                    url, params=cleaned, timeout=self.timeout
                )
                response.raise_for_status()
                return response.json() if response.text else {}
            except (RequestException, HTTPError) as exc:
                error = AlephException(exc)
                failures += 1
                if not error.transient or failures >= self.retries:
                    raise error from exc
                backoff(error, failures)

    # ------------------------------------------------------------------
    # instance metadata / followthemoney model
    # ------------------------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        """Instance metadata, including the followthemoney model. Cached."""
        if self._metadata is None:
            self._metadata = self.get("metadata")
        return self._metadata

    def model(self) -> dict[str, Any]:
        return self.metadata().get("model", {}) or {}

    def schemata(self) -> dict[str, Any]:
        return self.model().get("schemata", {}) or {}

    # ------------------------------------------------------------------
    # entities
    # ------------------------------------------------------------------

    def search(
        self,
        query: str | None = None,
        schema: str | None = None,
        countries: Sequence[str] | None = None,
        collection_ids: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        facets: Sequence[str] | None = None,
        facet_size: int = 10,
        highlight: bool = False,
        sort: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Full search envelope: ``total``, ``results``, ``facets``.

        ``alephclient.search()`` returns a paginating iterator that discards the
        facet block, so the raw endpoint is used instead.
        """
        params: dict[str, Any] = {
            "q": query,
            "limit": _clamp(limit, 1, MAX_LIMIT),
            "offset": max(0, offset),
        }
        # Default to Thing, matching alephclient.search(), so that intermediate
        # schemata (Ownership, Directorship, ...) don't crowd out real hits.
        params["filter:schemata"] = schema or "Thing"
        if countries:
            params["filter:countries"] = [c.lower() for c in countries]
        if collection_ids:
            params["filter:collection_id"] = list(collection_ids)
        for key, value in (filters or {}).items():
            params[f"filter:{key}"] = value
        if facets:
            params["facet"] = list(facets)
            for facet in facets:
                params[f"facet_size:{facet}"] = facet_size
                params[f"facet_total:{facet}"] = "true"
        if highlight:
            params["highlight"] = "true"
        if sort:
            params["sort"] = sort
        return self.get("entities", params)

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self.get(f"entities/{entity_id}")

    def expand_entity(
        self,
        entity_id: str,
        properties: Sequence[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Adjacent entities grouped by the property that connects them."""
        params: dict[str, Any] = {"limit": _clamp(limit, 1, MAX_LIMIT)}
        if properties:
            params["filter:property"] = list(properties)
        return self.get(f"entities/{entity_id}/expand", params)

    def similar_entities(self, entity_id: str, limit: int = 20) -> dict[str, Any]:
        """Candidate duplicates/matches of an entity across readable datasets."""
        return self.get(
            f"entities/{entity_id}/similar", {"limit": _clamp(limit, 1, MAX_LIMIT)}
        )

    # ------------------------------------------------------------------
    # collections
    # ------------------------------------------------------------------

    def list_collections(
        self,
        query: str | None = None,
        category: str | None = None,
        countries: Sequence[str] | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "limit": _clamp(limit, 1, MAX_LIMIT),
            "offset": max(0, offset),
        }
        if category:
            params["filter:category"] = category
        if countries:
            params["filter:countries"] = [c.lower() for c in countries]
        return self.get("collections", params)

    def get_collection(self, collection_id: str) -> dict[str, Any]:
        return self.get(f"collections/{collection_id}")

    def xref(
        self, collection_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """Previously computed cross-reference matches for a collection."""
        return self.get(
            f"collections/{collection_id}/xref",
            {"limit": _clamp(limit, 1, MAX_LIMIT), "offset": max(0, offset)},
        )

    def statistics(self) -> dict[str, Any]:
        return self.get("statistics")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _clean_params(params: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Flatten to (key, value) pairs, dropping empties and repeating lists."""
    pairs: list[tuple[str, Any]] = []
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            pairs.extend((key, item) for item in value if item not in (None, ""))
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, value))
    return pairs
