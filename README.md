# aleph-mcp

> [!WARNING]
> **This is a Claude one-shot.** The whole thing — server, formatters, tests,
> this README — was written by Claude in a single session, and has had no human
> review beyond the author reading it over. It works against a live instance
> (that much was verified), but treat it as a starting point rather than
> something battle-tested. Read the code before you point it at anything you
> care about.

A read-only MCP server for [Aleph](https://github.com/alephdata/aleph), the
open-source investigative data platform that holds leaks, company registries,
court records, sanctions lists and document archives. It gives an MCP client ten
research tools, built on the official
[`alephclient`](https://github.com/alephdata/alephclient) library.

It talks to **any** Aleph instance — set `ALEPH_HOST`. Out of the box it points
at [OpenAleph](https://search.openaleph.org), which answers without credentials.

**Read-only by design.** Every request is a `GET`. The server cannot create,
change or delete anything, and each tool is annotated `readOnlyHint` so clients
can present it as safe.

## Quickstart

Drop this in your MCP client's config — no clone, no venv. `uvx` fetches and
runs the server on demand:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/pkreissel/aleph-mcp", "aleph-mcp"],
      "env": {
        "ALEPH_HOST": "https://search.openaleph.org",
        "ALEPH_API_KEY": ""
      }
    }
  }
}
```

Leave `ALEPH_API_KEY` empty and it runs anonymously, which is enough for the
default instance ([OpenAleph](https://search.openaleph.org)). Point `ALEPH_HOST`
at an instance that needs credentials and paste the key in — that is the only
change.

The same thing from the Claude Code CLI:

```bash
claude mcp add aleph \
  --env ALEPH_HOST=https://search.openaleph.org \
  --env ALEPH_API_KEY= \
  -- uvx --from git+https://github.com/pkreissel/aleph-mcp aleph-mcp
```

Then ask your client to search for something. [`mcp.json`](mcp.json) in this
repo is the same config as a file, with a second entry showing a private
instance and an API key. You need [`uv`](https://docs.astral.sh/uv/) installed
(`brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`); the
first run takes a few seconds to resolve dependencies, later ones are fast.

## Install

The Quickstart needs none of this. These are the options if you want the
server on disk rather than resolved on demand.

### Run it without installing (uvx)

[`uv`](https://docs.astral.sh/uv/) can fetch, build and run the server in a
throwaway environment — nothing is left behind:

```bash
uvx --from git+https://github.com/pkreissel/aleph-mcp aleph-mcp
```

From a local checkout, the same thing without the clone:

```bash
uvx --from /path/to/aleph-mcp aleph-mcp
```

This is what the [Quickstart](#quickstart) config runs, and it keeps no venv
path that can go stale when you move the checkout.

### Install as a tool

Persistent, on your `PATH`, still isolated:

```bash
uv tool install git+https://github.com/pkreissel/aleph-mcp
```

`aleph-mcp` is then on your `PATH` and speaks MCP over stdio — point a client
at it by name rather than by path.

### From a checkout

For development, or if you want the source on hand:

```bash
git clone https://github.com/pkreissel/aleph-mcp
cd aleph-mcp
uv venv
uv pip install -e .
```

The entry point is then `.venv/bin/aleph-mcp`.

## Configure

| Variable | Required | Purpose |
| --- | --- | --- |
| `ALEPH_HOST` | no | Instance URL. Defaults to `https://search.openaleph.org`. |
| `ALEPH_API_KEY` | depends | API key for that instance. Also read from `ALEPHCLIENT_API_KEY` / `MEMORIOUS_ALEPH_API_KEY` by `alephclient`. |
| `ALEPH_MCP_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` (default), `ERROR`. Logs go to stderr. |

Whether you need a key depends on the instance. Most public ones reject
unauthenticated requests to everything except `/api/2/metadata`, so without a
key only `aleph_get_schema_info` works; get one from your profile page on that
instance, under **API Key**. OpenAleph serves anonymous requests, so no key is
needed there.

### Claude Code

```bash
claude mcp add aleph \
  --env ALEPH_HOST=https://your-instance.org \
  --env ALEPH_API_KEY=your-key \
  -- /path/to/aleph-mcp/.venv/bin/aleph-mcp
```

### Any client using `mcpServers` JSON

Against a checkout rather than `uvx` — see the [Quickstart](#quickstart) for the
`uvx` form:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "/path/to/aleph-mcp/.venv/bin/aleph-mcp",
      "env": {
        "ALEPH_HOST": "https://your-instance.org",
        "ALEPH_API_KEY": "your-key"
      }
    }
  }
}
```

Several instances at once is just several entries with different `ALEPH_HOST`
values and distinct server names.

## Tools

| Tool | Purpose |
| --- | --- |
| `aleph_search` | Search entities across every dataset you can read. Filter by schema, country and collection; request facets and highlighted snippets. |
| `aleph_get_entity` | Full record for one entity ID, every populated property. |
| `aleph_expand_entity` | Entities connected to this one, grouped by relationship type. |
| `aleph_similar_entities` | Candidate matches for the same real-world subject in other datasets. |
| `aleph_list_collections` | Discover datasets, filtered by name, category or country. |
| `aleph_get_collection` | Dataset metadata: publisher, source, coverage, update frequency, entity-type breakdown. |
| `aleph_xref_results` | Read cross-reference matches already computed for a dataset. |
| `aleph_statistics` | Instance size and shape, by entity type, category and country. |
| `aleph_get_schema_info` | followthemoney model reference: schemata and their properties. |
| `aleph_fetch_document_text` | Extracted text of a document entity, for quoting and verification. |

## How Aleph models data

Worth knowing, because it shapes how the tools chain together.

Everything is an **entity** with a **schema** (`Person`, `Company`, `Document`,
`Ownership`, …) belonging to a **collection** (a dataset). Schemata inherit:
`Person` extends `LegalEntity` extends `Thing`, and a search for `LegalEntity`
returns companies and people alike.

Relationships are themselves entities. A person is not linked to a company by a
field on the person; there is a separate `Directorship` entity pointing at both.
That is why `aleph_expand_entity` exists — you cannot see the network by reading
one record.

A typical investigation:

1. `aleph_search` for the subject, narrowed by `schema` and `countries`.
2. `aleph_get_entity` on the best hit for the full record.
3. `aleph_expand_entity` to walk to officers, owners and addresses.
4. `aleph_similar_entities` to find the same subject in other datasets.
5. `aleph_fetch_document_text` to read the source document behind a claim.

Long documents store their text on child `Page` entities rather than the parent
`Document`; if `aleph_fetch_document_text` finds no text, expand the document
and fetch a page.

Results are search hits, not verified facts. Datasets are full of namesakes —
confirm identity against birth dates, addresses or registration numbers before
treating two records as the same person.

## Output

Tools return compact text, not raw JSON. A page of Aleph results is tens of
kilobytes of index bookkeeping; each entity is rendered down to its caption,
schema, the featured properties for that schema, its source dataset, its entity
ID and a URL that opens the record in the instance's web UI. IDs and URLs are
always present, so every claim can be traced back to the source.

## Instance notes

Instances differ in more than their data.

**Bot filters.** Some sit behind a proof-of-work challenge —
`search.openaleph.org` uses [Anubis](https://anubis.techaro.lol/), which
`307`-redirects unrecognised clients to a challenge page instead of returning
JSON. It allowlists by `User-Agent`, and `alephclient` sends
`alephclient/<version>`, which passes; plain `curl` or `python-requests` does
not. So this server works as shipped, but anything that overrides the session
`User-Agent` will start getting HTML back.

**Reachable data.** What a search returns is a function of the instance and of
the key you gave it. `aleph_list_collections` and `aleph_statistics` are the
quickest way to see what an instance actually holds before searching it.

## Development

```bash
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The suite covers formatting, parameter construction, error mapping and an
end-to-end stdio handshake against the real server process. It needs no network
access and no API key: `tests/metadata.json` is a verbatim capture of a live
instance's `/api/2/metadata`, so formatters run against the real followthemoney
model.

## Layout

```
src/aleph_mcp/
  server.py   tool definitions, argument schemas, error messages
  client.py   read-only Aleph access layered on alephclient
  format.py   API payloads -> compact text
```

`alephclient` supplies host/key configuration, the auth header and the session.
It wraps only part of the API, so endpoints it does not cover (search with
facets, expand, similar, xref, statistics, metadata) are issued as raw `GET`s
over the same configured session.

## Licence

MIT.
