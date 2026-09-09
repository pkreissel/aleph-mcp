"""MCP server exposing read-only research tools for an Aleph instance."""

from __future__ import annotations

import functools
import logging
import os
from typing import Annotated, Any, Callable

import anyio
from alephclient.errors import AlephException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from aleph_mcp import format as fmt
from aleph_mcp.client import AlephReadClient

log = logging.getLogger(__name__)

DEFAULT_HOST = "https://search.openaleph.org"

INSTRUCTIONS = """\
Search an Aleph instance: an investigative archive of leaks, company registries,
court records, sanctions lists and other documents, modelled with followthemoney
(FtM). Which datasets are reachable depends on the instance this server is
pointed at and on the credentials it was given.

Everything in Aleph is an *entity* with a *schema* (Person, Company, Document,
Ownership, ...) belonging to a *collection* (a dataset). Relationships are
themselves entities: a Person is linked to a Company through a Directorship or
Ownership entity, not a direct field.

A typical investigation:
  1. aleph_search to find a subject; narrow with schema and countries.
  2. aleph_get_entity for the full record.
  3. aleph_expand_entity to walk relationships (officers, owners, addresses).
  4. aleph_similar_entities to find the same person across other datasets.
  5. aleph_fetch_document_text to read source documents behind a claim.

These tools read only; they cannot create, change or delete anything in Aleph.
Results are search hits, not verified facts — many datasets contain namesakes,
so confirm identity with dates of birth, addresses or identifiers before
concluding two records describe the same person.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

mcp: MCPServer = MCPServer(
    name="aleph",
    title="Aleph",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    website_url="https://openaleph.org",
)

_client: AlephReadClient | None = None


def get_client() -> AlephReadClient:
    """Build the Aleph client on first use, so import never needs credentials."""
    global _client
    if _client is None:
        host = os.environ.get("ALEPH_HOST") or DEFAULT_HOST
        # "" is a likely value (an env var declared but left blank); alephclient
        # would turn it into a malformed "ApiKey " header, so normalise it away.
        api_key = os.environ.get("ALEPH_API_KEY") or None
        if not api_key:
            log.info(
                "ALEPH_API_KEY is not set; %s will only return what it exposes "
                "anonymously. Many instances allow nothing but /api/2/metadata.",
                host,
            )
        _client = AlephReadClient(host=host, api_key=api_key)
    return _client


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a blocking alephclient call off the event loop, mapping API errors."""
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except AlephException as exc:
        raise ToolError(_explain(exc)) from exc


def _explain(exc: AlephException) -> str:
    host = os.environ.get("ALEPH_HOST") or DEFAULT_HOST
    if exc.status in (401, 403):
        configured = "set" if os.environ.get("ALEPH_API_KEY") else "NOT set"
        return (
            f"Aleph refused the request ({exc.status}). ALEPH_API_KEY is {configured}. "
            f"{host} may require an API key for anything beyond instance metadata; "
            "get one from your profile page on that instance, under 'API Key'. A 403 "
            "can also mean your account cannot read this particular collection."
        )
    if exc.status == 404:
        return f"Not found on {host}. Check the ID — it may be wrong, or in a collection you cannot read."
    if exc.status == 429:
        return "Aleph is rate-limiting this session. Wait a moment, then retry with a smaller limit."
    status = f" (HTTP {exc.status})" if exc.status else ""
    return f"Aleph request failed{status}: {exc.message}"


async def _schemata() -> dict[str, Any]:
    client = get_client()
    try:
        return await _call(client.schemata)
    except ToolError:
        # Formatting degrades gracefully without the model; don't fail the tool.
        return {}


# ----------------------------------------------------------------------
# entity search & retrieval
# ----------------------------------------------------------------------


@mcp.tool(
    description=(
        "Search entities (people, companies, documents, ...) across every Aleph "
        "dataset you can read. This is the main entry point. Returns compact "
        "summaries with entity IDs to pass to the other tools. Request facets to "
        "see which countries or datasets the matches cluster in."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_search(
    query: Annotated[
        str,
        Field(
            description=(
                "Free-text query. Supports quoted phrases (\"Igor Ivanov\"), "
                "boolean AND/OR/NOT and wildcards (gazprom*)."
            )
        ),
    ],
    schema: Annotated[
        str | None,
        Field(
            description=(
                "Restrict to one followthemoney schema and its descendants, e.g. "
                "Person, Company, LegalEntity, Document, Address. Defaults to "
                "Thing (all real-world things). Use aleph_get_schema_info to list them."
            )
        ),
    ] = None,
    countries: Annotated[
        list[str] | None,
        Field(description="Two-letter ISO country codes, e.g. ['ru', 'cy']."),
    ] = None,
    collection_ids: Annotated[
        list[str] | None,
        Field(description="Restrict to these collection (dataset) IDs."),
    ] = None,
    facets: Annotated[
        list[str] | None,
        Field(
            description=(
                "Aggregate the result set by these fields, e.g. "
                "['countries', 'schema', 'collection_id', 'names', 'addresses']."
            )
        ),
    ] = None,
    highlight: Annotated[
        bool, Field(description="Include matching text snippets for each hit.")
    ] = False,
    limit: Annotated[int, Field(description="Results per page (1-200).", ge=1, le=200)] = 20,
    offset: Annotated[int, Field(description="Skip this many results, for paging.", ge=0)] = 0,
) -> str:
    client = get_client()
    data = await _call(
        client.search,
        query=query,
        schema=schema,
        countries=countries,
        collection_ids=collection_ids,
        facets=facets,
        highlight=highlight,
        limit=limit,
        offset=offset,
    )
    return fmt.search_results(data, await _schemata(), client.host)


@mcp.tool(
    description=(
        "Fetch one entity by ID, with every populated property. Use after "
        "aleph_search to see the full record behind a hit."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_get_entity(
    entity_id: Annotated[str, Field(description="Entity ID from a search result.")],
) -> str:
    client = get_client()
    entity = await _call(client.get_entity, entity_id)
    return fmt.entity_detail(entity, await _schemata(), client.host)


@mcp.tool(
    description=(
        "List the entities connected to this one, grouped by relationship type "
        "(directorships, ownerships, addresses, family, document mentions). This "
        "is how you walk a network: relationships in Aleph are entities, so they "
        "do not appear as plain fields on the record itself."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_expand_entity(
    entity_id: Annotated[str, Field(description="Entity ID to expand.")],
    properties: Annotated[
        list[str] | None,
        Field(
            description=(
                "Only follow these property names, e.g. ['directorshipDirector', "
                "'ownershipOwner']. Omit to follow every relationship."
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max entities per relationship group.", ge=1, le=200)] = 20,
) -> str:
    client = get_client()
    data = await _call(client.expand_entity, entity_id, properties=properties, limit=limit)
    return fmt.expand_results(data, await _schemata(), client.host)


@mcp.tool(
    description=(
        "Find entities that may be the same real-world person or company as this "
        "one, across other datasets. Use it to link a subject between a leak and a "
        "company registry. Results are ranked candidates, not confirmed matches — "
        "verify with birth dates, addresses or registration numbers."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_similar_entities(
    entity_id: Annotated[str, Field(description="Entity ID to find matches for.")],
    limit: Annotated[int, Field(description="Max candidates to return.", ge=1, le=200)] = 20,
) -> str:
    client = get_client()
    data = await _call(client.similar_entities, entity_id, limit=limit)
    return fmt.similar_results(data, await _schemata(), client.host)


# ----------------------------------------------------------------------
# collections
# ----------------------------------------------------------------------


@mcp.tool(
    description=(
        "List or search the datasets (collections) available to you — leaks, "
        "company registries, sanctions lists, court archives. Use it to discover "
        "what sources exist, then pass collection IDs to aleph_search to scope a query."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_list_collections(
    query: Annotated[
        str | None, Field(description="Filter datasets by name, e.g. 'Cyprus'.")
    ] = None,
    category: Annotated[
        str | None,
        Field(
            description=(
                "Dataset category: leak, company, sanctions, court, procurement, "
                "land, gazette, news, casefile, poi, regulatory, customs, transport, "
                "finance, license, library, census, grey, other."
            )
        ),
    ] = None,
    countries: Annotated[
        list[str] | None, Field(description="Two-letter ISO country codes.")
    ] = None,
    limit: Annotated[int, Field(description="Datasets per page (1-200).", ge=1, le=200)] = 30,
    offset: Annotated[int, Field(description="Skip this many, for paging.", ge=0)] = 0,
) -> str:
    client = get_client()
    data = await _call(
        client.list_collections,
        query=query,
        category=category,
        countries=countries,
        limit=limit,
        offset=offset,
    )
    return fmt.collection_results(data, client.host)


@mcp.tool(
    description=(
        "Full metadata for one dataset: publisher, source URL, coverage, update "
        "frequency and a breakdown of what entity types it contains. Use it to "
        "judge how far a source can be trusted and how current it is."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_get_collection(
    collection_id: Annotated[str, Field(description="Collection (dataset) ID.")],
) -> str:
    client = get_client()
    collection = await _call(client.get_collection, collection_id)
    return fmt.collection_detail(collection, client.host)


@mcp.tool(
    description=(
        "Read the cross-reference results already computed for a dataset: entities "
        "in it that resemble entities elsewhere in Aleph, scored and ranked. Useful "
        "for finding overlaps between your own casefile and public registries. "
        "Read-only — this does not start a new cross-reference run."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_xref_results(
    collection_id: Annotated[str, Field(description="Collection ID to read xref results for.")],
    limit: Annotated[int, Field(description="Matches per page (1-200).", ge=1, le=200)] = 20,
    offset: Annotated[int, Field(description="Skip this many, for paging.", ge=0)] = 0,
) -> str:
    client = get_client()
    data = await _call(client.xref, collection_id, limit=limit, offset=offset)
    return fmt.xref_results(data, await _schemata(), client.host)


# ----------------------------------------------------------------------
# reference & documents
# ----------------------------------------------------------------------


@mcp.tool(
    description=(
        "Size and shape of the Aleph instance: how many datasets and entities it "
        "holds, broken down by entity type, category and country. Use it to gauge "
        "coverage before concluding something is absent."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_statistics() -> str:
    return fmt.statistics_summary(await _call(get_client().statistics))


@mcp.tool(
    description=(
        "Reference for the followthemoney data model this instance uses. Call with "
        "no argument for the list of schemata; call with a name (Person, Company, "
        "Ownership, ...) for its properties and how it links to other schemata. "
        "Use it to pick the right schema filter or expand property."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_get_schema_info(
    schema: Annotated[
        str | None,
        Field(description="Schema name, e.g. 'Person'. Omit to list all schemata."),
    ] = None,
) -> str:
    return fmt.schema_info(await _schemata(), schema)


@mcp.tool(
    description=(
        "Read the extracted text of a document entity (PDF page, email, article) so "
        "you can quote or verify what it actually says. Pass the entity ID of a "
        "Document, Pages, Email or similar entity from a search result."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def aleph_fetch_document_text(
    entity_id: Annotated[str, Field(description="Entity ID of the document.")],
    max_chars: Annotated[
        int,
        Field(description="Truncate the text at this many characters.", ge=500, le=200_000),
    ] = 20_000,
) -> str:
    client = get_client()
    entity = await _call(client.get_entity, entity_id)
    return fmt.document_text(entity, await _schemata(), client.host, max_chars)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("ALEPH_MCP_LOG_LEVEL", "WARNING").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
