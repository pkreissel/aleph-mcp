"""Client tests: URL/parameter construction, read-only guarantee, error mapping."""

import pytest
from alephclient.errors import AlephException
from requests.exceptions import ConnectionError as RequestsConnectionError, HTTPError

from aleph_mcp.client import AlephReadClient, _clean_params


class FakeResponse:
    def __init__(self, payload=None, status=200, text="{}"):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} Error", response=self)


class FakeSession:
    """Records requests instead of making them."""

    def __init__(self, responses=None):
        self.headers = {}
        self.calls = []
        self._responses = list(responses or [])

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self._responses:
            result = self._responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return FakeResponse({"status": "ok", "results": []})


@pytest.fixture
def client():
    c = AlephReadClient(host="https://aleph.example.org", api_key="secret")
    c.api.session = FakeSession()
    return c


def params_of(client, index=0):
    """The recorded params as a dict of key -> value or list of values."""
    out = {}
    for key, value in client.api.session.calls[index]["params"]:
        if key in out:
            out[key] = (out[key] if isinstance(out[key], list) else [out[key]]) + [value]
        else:
            out[key] = value
    return out


class TestConfiguration:
    def test_sets_api_key_header_via_alephclient(self):
        c = AlephReadClient(host="https://aleph.example.org", api_key="secret")
        assert c.api.session.headers["Authorization"] == "ApiKey secret"
        assert c.authenticated

    def test_reports_unauthenticated_without_key(self):
        c = AlephReadClient(host="https://aleph.example.org", api_key=None)
        assert not c.authenticated

    def test_derives_base_and_host_urls(self, client):
        assert client.base_url == "https://aleph.example.org/api/2/"
        assert client.host == "https://aleph.example.org/"

    def test_requires_a_host(self, monkeypatch):
        monkeypatch.delenv("ALEPH_HOST", raising=False)
        monkeypatch.delenv("ALEPHCLIENT_HOST", raising=False)
        monkeypatch.delenv("MEMORIOUS_ALEPH_HOST", raising=False)
        with pytest.raises(AlephException):
            AlephReadClient(host=None)


class TestSearchParameters:
    def test_builds_filters_facets_and_paging(self, client):
        client.search(
            query="Petrov",
            schema="Person",
            countries=["RU", "Cy"],
            collection_ids=["c1", "c2"],
            facets=["countries"],
            facet_size=5,
            highlight=True,
            sort="created_at:desc",
            limit=25,
            offset=50,
        )
        call = client.api.session.calls[0]
        assert call["url"] == "https://aleph.example.org/api/2/entities"
        p = params_of(client)
        assert p["q"] == "Petrov"
        assert p["filter:schemata"] == "Person"
        assert p["filter:countries"] == ["ru", "cy"]  # lowercased for Aleph
        assert p["filter:collection_id"] == ["c1", "c2"]
        assert p["facet"] == "countries"
        assert p["facet_size:countries"] == 5
        assert p["highlight"] == "true"
        assert p["sort"] == "created_at:desc"
        assert p["limit"] == 25 and p["offset"] == 50

    def test_defaults_to_thing_like_alephclient(self, client):
        client.search(query="x")
        assert params_of(client)["filter:schemata"] == "Thing"

    def test_omits_absent_options(self, client):
        client.search(query="x")
        keys = params_of(client).keys()
        assert "highlight" not in keys and "facet" not in keys
        assert "filter:countries" not in keys

    def test_arbitrary_filters_are_prefixed(self, client):
        client.search(query="x", filters={"addresses": "Nicosia"})
        assert params_of(client)["filter:addresses"] == "Nicosia"

    @pytest.mark.parametrize("given,expected", [(0, 1), (5000, 200), (50, 50)])
    def test_clamps_limit(self, client, given, expected):
        client.search(query="x", limit=given)
        assert params_of(client)["limit"] == expected

    def test_clamps_negative_offset(self, client):
        client.search(query="x", offset=-10)
        assert params_of(client)["offset"] == 0


class TestOtherEndpoints:
    def test_entity_paths(self, client):
        client.get_entity("e1")
        client.expand_entity("e1", properties=["director"], limit=5)
        client.similar_entities("e1", limit=3)
        urls = [c["url"] for c in client.api.session.calls]
        assert urls == [
            "https://aleph.example.org/api/2/entities/e1",
            "https://aleph.example.org/api/2/entities/e1/expand",
            "https://aleph.example.org/api/2/entities/e1/similar",
        ]
        assert params_of(client, 1)["filter:property"] == "director"

    def test_collection_paths_and_filters(self, client):
        client.list_collections(query="cyprus", category="company", countries=["CY"])
        client.get_collection("c1")
        client.xref("c1", limit=10, offset=20)
        urls = [c["url"] for c in client.api.session.calls]
        assert urls[1] == "https://aleph.example.org/api/2/collections/c1"
        assert urls[2] == "https://aleph.example.org/api/2/collections/c1/xref"
        p = params_of(client, 0)
        assert p["filter:category"] == "company" and p["filter:countries"] == "cy"

    def test_metadata_is_cached(self, client):
        client.api.session._responses = [FakeResponse({"model": {"schemata": {"Person": {}}}})]
        assert client.schemata() == {"Person": {}}
        assert client.schemata() == {"Person": {}}
        assert len(client.api.session.calls) == 1  # second call served from cache


class TestErrorHandling:
    def test_maps_http_error_to_aleph_exception(self, client):
        client.api.session._responses = [FakeResponse(status=403, text="nope")]
        with pytest.raises(AlephException) as excinfo:
            client.get_entity("e1")
        assert excinfo.value.status == 403

    def test_does_not_retry_client_errors(self, client):
        client.api.session._responses = [FakeResponse(status=404, text="x")]
        with pytest.raises(AlephException):
            client.get_entity("e1")
        assert len(client.api.session.calls) == 1

    def test_retries_transient_errors_then_succeeds(self, client, monkeypatch):
        monkeypatch.setattr("aleph_mcp.client.backoff", lambda err, n: None)
        client.api.session._responses = [
            RequestsConnectionError("boom"),
            FakeResponse({"status": "ok", "id": "e1"}),
        ]
        assert client.get_entity("e1")["id"] == "e1"
        assert len(client.api.session.calls) == 2

    def test_gives_up_after_retry_budget(self, client, monkeypatch):
        monkeypatch.setattr("aleph_mcp.client.backoff", lambda err, n: None)
        client.retries = 3
        client.api.session._responses = [RequestsConnectionError("boom")] * 5
        with pytest.raises(AlephException):
            client.get_entity("e1")
        assert len(client.api.session.calls) == 3

    def test_empty_body_returns_empty_dict(self, client):
        client.api.session._responses = [FakeResponse(None, text="")]
        assert client.get_entity("e1") == {}


class TestReadOnly:
    def test_client_exposes_no_mutating_verbs(self, client):
        """Every request goes through session.get; nothing can write to Aleph."""
        for method in ("search", "get_entity", "expand_entity", "similar_entities",
                       "list_collections", "get_collection", "xref", "statistics",
                       "metadata"):
            client.api.session.calls.clear()
            getattr(client, method)(*(["x"] if method in {
                "get_entity", "expand_entity", "similar_entities",
                "get_collection", "xref"} else []))
        # FakeSession only implements get(); a POST/DELETE would have raised.
        assert not hasattr(client.api.session, "post")


class TestCleanParams:
    def test_drops_empty_and_expands_lists(self):
        pairs = _clean_params({"a": None, "b": "", "c": ["x", "", None, "y"], "d": 1})
        assert pairs == [("c", "x"), ("c", "y"), ("d", 1)]

    def test_serialises_booleans_for_aleph(self):
        assert _clean_params({"a": True, "b": False}) == [("a", "true"), ("b", "false")]

    def test_keeps_zero(self):
        assert ("offset", 0) in _clean_params({"offset": 0})
