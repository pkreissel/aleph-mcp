"""Server tests: tool surface, argument plumbing, and error messages."""

import pytest
from alephclient.errors import AlephException
from mcp.server.mcpserver.exceptions import ToolError

from aleph_mcp import server


@pytest.fixture
def stub(monkeypatch, schemata):
    """Replace the Aleph client with a recorder returning canned payloads."""

    class StubClient:
        host = "https://aleph.example.org/"

        def __init__(self):
            self.calls = []
            self.payloads = {}

        def _record(self, name, **kwargs):
            self.calls.append((name, kwargs))
            return self.payloads.get(name, {"status": "ok", "results": []})

        def schemata(self):
            return schemata

        def search(self, **kw):
            return self._record("search", **kw)

        def get_entity(self, entity_id):
            return self._record("get_entity", entity_id=entity_id)

        def expand_entity(self, entity_id, properties=None, limit=20):
            return self._record("expand_entity", entity_id=entity_id,
                                properties=properties, limit=limit)

        def similar_entities(self, entity_id, limit=20):
            return self._record("similar_entities", entity_id=entity_id, limit=limit)

        def list_collections(self, **kw):
            return self._record("list_collections", **kw)

        def get_collection(self, collection_id):
            return self._record("get_collection", collection_id=collection_id)

        def xref(self, collection_id, limit=20, offset=0):
            return self._record("xref", collection_id=collection_id,
                                limit=limit, offset=offset)

        def statistics(self):
            return self._record("statistics")

    client = StubClient()
    monkeypatch.setattr(server, "get_client", lambda: client)
    return client


async def call(name, **kwargs):
    result = await server.mcp.call_tool(name, kwargs)
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


EXPECTED_TOOLS = {
    "aleph_search",
    "aleph_get_entity",
    "aleph_expand_entity",
    "aleph_similar_entities",
    "aleph_list_collections",
    "aleph_get_collection",
    "aleph_xref_results",
    "aleph_statistics",
    "aleph_get_schema_info",
    "aleph_fetch_document_text",
}


class TestToolSurface:
    async def test_exposes_exactly_the_read_only_toolset(self):
        tools = await server.mcp.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS

    async def test_every_tool_is_annotated_read_only(self):
        for tool in await server.mcp.list_tools():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is True, tool.name
            assert tool.annotations.destructive_hint is False, tool.name

    async def test_every_tool_is_described(self):
        for tool in await server.mcp.list_tools():
            assert tool.description and len(tool.description) > 60, tool.name

    async def test_every_parameter_is_described(self):
        for tool in await server.mcp.list_tools():
            for name, spec in tool.input_schema.get("properties", {}).items():
                assert spec.get("description"), f"{tool.name}.{name}"

    async def test_server_carries_orientation_instructions(self):
        assert "followthemoney" in server.mcp.instructions
        assert "read only" in server.mcp.instructions


class TestArgumentPlumbing:
    async def test_search_forwards_every_option(self, stub):
        await call(
            "aleph_search",
            query="Petrov",
            schema="Person",
            countries=["ru"],
            collection_ids=["c1"],
            facets=["countries"],
            highlight=True,
            limit=5,
            offset=10,
        )
        name, kw = stub.calls[0]
        assert name == "search"
        assert kw == {
            "query": "Petrov", "schema": "Person", "countries": ["ru"],
            "collection_ids": ["c1"], "facets": ["countries"],
            "highlight": True, "limit": 5, "offset": 10,
        }

    async def test_expand_forwards_property_filter(self, stub):
        await call("aleph_expand_entity", entity_id="e1",
                   properties=["ownershipOwner"], limit=3)
        assert stub.calls[0] == (
            "expand_entity",
            {"entity_id": "e1", "properties": ["ownershipOwner"], "limit": 3},
        )

    async def test_document_text_fetches_the_entity(self, stub):
        stub.payloads["get_entity"] = {
            "id": "d1", "schema": "Pages", "caption": "memo.pdf",
            "properties": {"bodyText": ["Secret memo body."]},
        }
        out = await call("aleph_fetch_document_text", entity_id="d1")
        assert stub.calls[0] == ("get_entity", {"entity_id": "d1"})
        assert "Secret memo body." in out

    async def test_schema_info_needs_no_arguments(self, stub):
        out = await call("aleph_get_schema_info")
        assert "followthemoney schemata" in out


class TestValidation:
    @pytest.mark.parametrize("limit", [0, 500])
    async def test_rejects_out_of_range_limit(self, stub, limit):
        with pytest.raises(Exception):
            await call("aleph_search", query="x", limit=limit)

    async def test_rejects_negative_offset(self, stub):
        with pytest.raises(Exception):
            await call("aleph_search", query="x", offset=-1)

    async def test_requires_a_query(self, stub):
        with pytest.raises(Exception):
            await call("aleph_search")


class TestErrorMessages:
    def _fail(self, monkeypatch, stub, status):
        response = type("R", (), {"status_code": status,
                                  "json": lambda self: {"message": "denied"}})()
        exc = type("E", (Exception,), {})()
        exc.response = response
        error = AlephException(exc)

        def boom(**kwargs):
            raise error

        monkeypatch.setattr(stub, "search", boom)

    async def test_401_names_the_env_var_and_where_to_get_a_key(self, monkeypatch, stub):
        self._fail(monkeypatch, stub, 401)
        monkeypatch.delenv("ALEPH_API_KEY", raising=False)
        with pytest.raises(ToolError) as excinfo:
            await call("aleph_search", query="x")
        message = str(excinfo.value)
        assert "ALEPH_API_KEY is NOT set" in message
        assert "API Key" in message

    async def test_401_says_key_is_set_when_it_is(self, monkeypatch, stub):
        self._fail(monkeypatch, stub, 401)
        monkeypatch.setenv("ALEPH_API_KEY", "k")
        with pytest.raises(ToolError) as excinfo:
            await call("aleph_search", query="x")
        assert "ALEPH_API_KEY is set" in str(excinfo.value)

    async def test_404_suggests_the_likely_causes(self, monkeypatch, stub):
        self._fail(monkeypatch, stub, 404)
        with pytest.raises(ToolError) as excinfo:
            await call("aleph_search", query="x")
        assert "Check the ID" in str(excinfo.value)

    async def test_429_tells_the_caller_to_back_off(self, monkeypatch, stub):
        self._fail(monkeypatch, stub, 429)
        with pytest.raises(ToolError) as excinfo:
            await call("aleph_search", query="x")
        assert "rate-limiting" in str(excinfo.value)

    async def test_formatting_survives_metadata_failure(self, monkeypatch, stub):
        """A dead /metadata must not take down search."""
        def boom():
            raise AlephException(Exception("metadata down"))

        monkeypatch.setattr(stub, "schemata", boom)
        stub.payloads["search"] = {
            "total": 1, "offset": 0,
            "results": [{"id": "e1", "schema": "Person", "caption": "Ivan"}],
        }
        out = await call("aleph_search", query="x")
        assert "Ivan — Person" in out
        assert "Entity ID: e1" in out


class TestClientConstruction:
    def test_defaults_to_openaleph(self, monkeypatch):
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.delenv("ALEPH_HOST", raising=False)
        monkeypatch.setenv("ALEPH_API_KEY", "k")
        assert server.get_client().host == "https://search.openaleph.org/"

    def test_honours_a_custom_host(self, monkeypatch):
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.setenv("ALEPH_HOST", "https://aleph.example.org")
        assert server.get_client().host == "https://aleph.example.org/"

    def test_builds_without_a_key_so_import_never_fails(self, monkeypatch):
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.delenv("ALEPH_API_KEY", raising=False)
        assert server.get_client().authenticated is False
