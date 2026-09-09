"""Render Aleph API payloads as compact text.

Raw Aleph entities are verbose and deeply nested; a page of 30 results is tens
of kilobytes of JSON that is mostly index bookkeeping. These helpers flatten
them into short, readable blocks that keep the parts an investigator needs:
the caption, the schema, the featured properties for that schema, the dataset
the record came from, and a URL to open it in Aleph.
"""

from __future__ import annotations

import difflib
from typing import Any, Mapping

MAX_VALUE_CHARS = 240
MAX_VALUES_PER_PROP = 5


# ----------------------------------------------------------------------
# scalars
# ----------------------------------------------------------------------


def _truncate(text: str, limit: int = MAX_VALUE_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _value(value: Any) -> str:
    """Render one property value; entity-typed values may be nested dicts."""
    if isinstance(value, Mapping):
        caption = value.get("caption") or value.get("id") or ""
        schema = value.get("schema")
        return f"{caption} [{schema}]" if schema and caption else str(caption)
    return _truncate(value)


def _values(values: Any) -> str:
    items = values if isinstance(values, (list, tuple)) else [values]
    rendered = [_value(v) for v in items[:MAX_VALUES_PER_PROP] if v not in (None, "")]
    extra = len(items) - MAX_VALUES_PER_PROP
    if extra > 0:
        rendered.append(f"(+{extra} more)")
    return "; ".join(rendered)


# ----------------------------------------------------------------------
# model lookups
# ----------------------------------------------------------------------


def _schema_def(schemata: Mapping[str, Any], name: str | None) -> dict[str, Any]:
    if not name:
        return {}
    return schemata.get(name, {}) or {}


def _all_properties(schemata: Mapping[str, Any], schema: str | None) -> dict[str, Any]:
    """Own properties merged with inherited ones.

    The model served by /api/2/metadata does not flatten inheritance: Person
    carries `birthDate` but not `name`, which is defined on Thing. Each schema
    lists its ancestors under "schemata", so walk those and let the schema's own
    definitions win on conflict.
    """
    schema_def = _schema_def(schemata, schema)
    merged: dict[str, Any] = {}
    for ancestor in schema_def.get("schemata") or []:
        if ancestor != schema:
            merged.update(_schema_def(schemata, ancestor).get("properties") or {})
    merged.update(schema_def.get("properties") or {})
    return merged


def _prop_label(schemata: Mapping[str, Any], schema: str | None, prop: str) -> str:
    spec = _all_properties(schemata, schema).get(prop) or {}
    return spec.get("label", prop)


def _featured(schemata: Mapping[str, Any], schema: str | None) -> list[str]:
    return list(_schema_def(schemata, schema).get("featured") or [])


def _caption(entity: Mapping[str, Any], schemata: Mapping[str, Any]) -> str:
    caption = entity.get("caption")
    if caption:
        return _truncate(caption, 120)
    props = entity.get("properties", {}) or {}
    for key in _schema_def(schemata, entity.get("schema")).get("caption") or []:
        if props.get(key):
            return _truncate(_values(props[key]), 120)
    return "(untitled)"


# ----------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------


def entity_url(entity: Mapping[str, Any], host: str) -> str:
    ui = (entity.get("links") or {}).get("ui")
    return ui or f"{host.rstrip('/')}/entities/{entity.get('id', '')}"


def collection_url(collection: Mapping[str, Any], host: str) -> str:
    ui = (collection.get("links") or {}).get("ui")
    return ui or f"{host.rstrip('/')}/datasets/{collection.get('id', '')}"


# ----------------------------------------------------------------------
# entities
# ----------------------------------------------------------------------


def entity_summary(
    entity: Mapping[str, Any],
    schemata: Mapping[str, Any],
    host: str,
    index: int | None = None,
) -> str:
    """A few lines per entity: caption, schema, featured props, source, URL."""
    schema = entity.get("schema")
    head = f"{_caption(entity, schemata)} — {schema}"
    lines = [f"{index}. {head}" if index is not None else head]

    props = entity.get("properties", {}) or {}
    for prop in _featured(schemata, schema):
        if props.get(prop):
            lines.append(
                f"   {_prop_label(schemata, schema, prop)}: {_values(props[prop])}"
            )

    collection = entity.get("collection") or {}
    if collection.get("label"):
        lines.append(f"   Dataset: {collection['label']} (id {collection.get('id')})")

    for snippet in (entity.get("highlight") or [])[:2]:
        lines.append(f"   Match: …{_truncate(snippet, 200)}…")

    lines.append(f"   Entity ID: {entity.get('id')}")
    lines.append(f"   URL: {entity_url(entity, host)}")
    return "\n".join(lines)


def entity_detail(
    entity: Mapping[str, Any], schemata: Mapping[str, Any], host: str
) -> str:
    """Every populated property, not just the featured ones."""
    schema = entity.get("schema")
    schema_def = _schema_def(schemata, schema)
    lines = [
        f"# {_caption(entity, schemata)}",
        f"Schema: {schema} — {schema_def.get('description', '')}".rstrip(" —"),
        f"Entity ID: {entity.get('id')}",
        f"URL: {entity_url(entity, host)}",
    ]

    collection = entity.get("collection") or {}
    if collection:
        lines.append(
            f"Dataset: {collection.get('label')} (id {collection.get('id')}, "
            f"category {collection.get('category')})"
        )

    props = entity.get("properties", {}) or {}
    if props:
        lines.append("\n## Properties")
        featured = _featured(schemata, schema)
        ordered = featured + sorted(k for k in props if k not in featured)
        for prop in ordered:
            if not props.get(prop):
                continue
            lines.append(f"- {_prop_label(schemata, schema, prop)}: {_values(props[prop])}")

    for label, key in (("First seen", "first_seen"), ("Last seen", "last_seen")):
        if entity.get(key):
            lines.append(f"{label}: {entity[key]}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# result envelopes
# ----------------------------------------------------------------------


def _pagination(data: Mapping[str, Any]) -> str:
    total = data.get("total")
    if isinstance(total, Mapping):  # some endpoints return {"value":N,"relation":..}
        total = total.get("value")
    offset = data.get("offset", 0) or 0
    shown = len(data.get("results") or [])
    if total is None:
        return f"Showing {shown} result(s) from offset {offset}."
    line = f"Showing {shown} of {total} result(s), offset {offset}."
    if isinstance(total, int) and offset + shown < total:
        line += f" Use offset={offset + shown} for the next page."
    return line


def facets_summary(data: Mapping[str, Any]) -> str:
    facets = data.get("facets") or {}
    if not facets:
        return ""
    blocks = ["\n## Facets"]
    for name, facet in facets.items():
        values = (facet or {}).get("values") or []
        if not values:
            continue
        label = (facet or {}).get("label", name)
        rendered = ", ".join(
            f"{v.get('label', v.get('id'))} ({v.get('count')})" for v in values
        )
        blocks.append(f"- {label} [{name}]: {rendered}")
    return "\n".join(blocks) if len(blocks) > 1 else ""


def search_results(
    data: Mapping[str, Any], schemata: Mapping[str, Any], host: str
) -> str:
    results = data.get("results") or []
    if not results:
        return "No results.\n\nTry a broader query, or drop the schema/country filters."
    offset = data.get("offset", 0) or 0
    blocks = [_pagination(data), ""]
    blocks += [
        entity_summary(e, schemata, host, index=offset + i + 1)
        for i, e in enumerate(results)
    ]
    facets = facets_summary(data)
    if facets:
        blocks.append(facets)
    return "\n\n".join(b for b in blocks if b)


def expand_results(
    data: Mapping[str, Any], schemata: Mapping[str, Any], host: str
) -> str:
    """Adjacent entities, grouped by the property that links them."""
    groups = data.get("results") or []
    if not groups:
        return "No relationships found for this entity."
    blocks = [f"Related entities across {len(groups)} relationship type(s)."]
    for group in groups:
        prop = group.get("property") or group.get("qname") or "related"
        count = group.get("count")
        entities = group.get("entities") or []
        heading = f"\n## {prop}" + (f" ({count} total)" if count is not None else "")
        blocks.append(heading)
        if not entities:
            blocks.append("   (no entities returned for this group)")
        for i, entity in enumerate(entities, 1):
            blocks.append(entity_summary(entity, schemata, host, index=i))
    return "\n\n".join(blocks)


def similar_results(
    data: Mapping[str, Any], schemata: Mapping[str, Any], host: str
) -> str:
    results = data.get("results") or []
    if not results:
        return "No similar entities found."
    blocks = [
        _pagination(data),
        "Scores are Aleph's similarity ranking, not a confirmed identity match.",
        "",
    ]
    for i, item in enumerate(results, 1):
        # Some deployments nest the candidate under "entity"; others return it flat.
        entity = item.get("entity") if isinstance(item.get("entity"), Mapping) else item
        block = entity_summary(entity, schemata, host, index=i)
        score = item.get("score")
        if score is not None:
            block += f"\n   Score: {score}"
        blocks.append(block)
    return "\n\n".join(blocks)


def xref_results(
    data: Mapping[str, Any], schemata: Mapping[str, Any], host: str
) -> str:
    results = data.get("results") or []
    if not results:
        return (
            "No cross-reference results. Either none have been computed for this "
            "collection, or you lack permission to read them."
        )
    blocks = [_pagination(data), ""]
    for i, item in enumerate(results, 1):
        entity = item.get("entity") or {}
        match = item.get("match") or {}
        match_collection = item.get("match_collection") or {}
        blocks.append(
            "\n".join(
                [
                    f"{i}. score {item.get('score')} — decision: {item.get('decision', 'undecided')}",
                    f"   Source: {_caption(entity, schemata)} [{entity.get('schema')}] "
                    f"({entity.get('id')})",
                    f"   Match:  {_caption(match, schemata)} [{match.get('schema')}] "
                    f"({match.get('id')})",
                    f"   Match dataset: {match_collection.get('label', 'unknown')}",
                    f"   URL: {entity_url(match, host)}",
                ]
            )
        )
    return "\n\n".join(blocks)


# ----------------------------------------------------------------------
# collections
# ----------------------------------------------------------------------


def collection_summary(
    collection: Mapping[str, Any], host: str, index: int | None = None
) -> str:
    head = collection.get("label") or collection.get("foreign_id") or "(unnamed)"
    lines = [f"{index}. {head}" if index is not None else head]
    for label, key in (
        ("Category", "category"),
        ("Countries", "countries"),
        ("Updated", "updated_at"),
        ("Frequency", "frequency"),
    ):
        if collection.get(key):
            lines.append(f"   {label}: {_values(collection[key])}")
    if collection.get("count") is not None:
        lines.append(f"   Entities: {collection['count']}")
    lines.append(f"   Collection ID: {collection.get('id')}")
    lines.append(f"   URL: {collection_url(collection, host)}")
    return "\n".join(lines)


def collection_detail(collection: Mapping[str, Any], host: str) -> str:
    lines = [
        f"# {collection.get('label')}",
        f"Collection ID: {collection.get('id')}",
        f"Foreign ID: {collection.get('foreign_id')}",
        f"URL: {collection_url(collection, host)}",
    ]
    for label, key in (
        ("Category", "category"),
        ("Countries", "countries"),
        ("Languages", "languages"),
        ("Publisher", "publisher"),
        ("Publisher URL", "publisher_url"),
        ("Data source URL", "data_url"),
        ("Update frequency", "frequency"),
        ("Updated at", "updated_at"),
        ("Entity count", "count"),
        ("Restricted", "restricted"),
    ):
        if collection.get(key) not in (None, "", []):
            lines.append(f"{label}: {_values(collection[key])}")
    if collection.get("summary"):
        lines.append(f"\n## Summary\n{collection['summary']}")
    stats = collection.get("statistics") or {}
    schema_stats = (stats.get("schema") or {}).get("values") or []
    if schema_stats:
        lines.append("\n## Entity types")
        lines += [
            f"- {v.get('label', v.get('id'))}: {v.get('count')}" for v in schema_stats
        ]
    return "\n".join(lines)


def collection_results(data: Mapping[str, Any], host: str) -> str:
    results = data.get("results") or []
    if not results:
        return "No collections matched."
    offset = data.get("offset", 0) or 0
    blocks = [_pagination(data), ""]
    blocks += [
        collection_summary(c, host, index=offset + i + 1)
        for i, c in enumerate(results)
    ]
    return "\n\n".join(blocks)


# ----------------------------------------------------------------------
# statistics & schema reference
# ----------------------------------------------------------------------


def statistics_summary(data: Mapping[str, Any]) -> str:
    lines = ["# Aleph instance statistics"]
    for label, key in (("Collections", "collections"), ("Entities", "things")):
        if data.get(key) is not None:
            lines.append(f"{label}: {data[key]}")

    for key, heading in (
        ("schemata", "Entity types"),
        ("categories", "Dataset categories"),
        ("countries", "Countries"),
    ):
        block = data.get(key) or {}
        values = block.get("values") if isinstance(block, Mapping) else None
        if values:
            rows = [(v.get("label", v.get("id")), v.get("count")) for v in values[:25]]
        elif isinstance(block, Mapping):
            rows = [
                (k, v.get("count") if isinstance(v, Mapping) else v)
                for k, v in list(block.items())[:25]
            ]
        else:
            continue
        lines.append(f"\n## {heading}")
        lines += [f"- {name}: {count}" for name, count in rows]
    return "\n".join(lines)


def schema_info(schemata: Mapping[str, Any], name: str | None) -> str:
    """Reference for one followthemoney schema, or an index of all of them."""
    if not name:
        matchable = sorted(k for k, v in schemata.items() if (v or {}).get("matchable"))
        others = sorted(k for k in schemata if k not in matchable)
        return "\n".join(
            [
                f"{len(schemata)} followthemoney schemata on this instance.",
                "",
                "## Matchable (searchable as real-world things)",
                ", ".join(matchable),
                "",
                "## Other (documents, relationships, intervals)",
                ", ".join(others),
                "",
                "Call this tool with a schema name for its properties.",
            ]
        )

    schema = schemata.get(name)
    if schema is None:
        # Schema names are case-sensitive in Aleph, so accept a near miss and
        # point at the real name rather than returning a bare failure.
        substring = [k for k in sorted(schemata) if name.lower() in k.lower()]
        fuzzy = difflib.get_close_matches(name, sorted(schemata), n=5, cutoff=0.6)
        close = list(dict.fromkeys(substring + fuzzy))
        hint = f" Did you mean: {', '.join(close[:5])}?" if close else ""
        return f"Unknown schema '{name}'.{hint}"

    lines = [
        f"# {name} — {schema.get('label')}",
        schema.get("description") or "",
        f"Extends: {', '.join(schema.get('extends') or []) or '(none)'}",
        f"Matchable: {bool(schema.get('matchable'))}",
        f"Featured properties: {', '.join(schema.get('featured') or []) or '(none)'}",
        "",
        "## Properties",
    ]
    own = set(schema.get("properties") or {})
    for prop, spec in sorted(_all_properties(schemata, name).items()):
        spec = spec or {}
        bits = [f"type={spec.get('type')}"]
        if spec.get("range"):
            bits.append(f"range={spec['range']}")
        if spec.get("matchable"):
            bits.append("matchable")
        if prop not in own:
            bits.append("inherited")
        lines.append(f"- {prop} ({spec.get('label')}) — {', '.join(bits)}")
    return "\n".join(line for line in lines if line is not None)


# ----------------------------------------------------------------------
# documents
# ----------------------------------------------------------------------

TEXT_PROPS = ("bodyText", "indexText", "bodyHtml")


def document_text(
    entity: Mapping[str, Any], schemata: Mapping[str, Any], host: str, max_chars: int
) -> str:
    """Extracted text of a document entity, with a short header for context."""
    props = entity.get("properties", {}) or {}
    chunks: list[str] = []
    for prop in TEXT_PROPS:
        for value in props.get(prop) or []:
            if isinstance(value, str) and value.strip():
                chunks.append(value)
        if chunks:
            break

    header = [
        f"# {_caption(entity, schemata)}",
        f"Schema: {entity.get('schema')}",
        f"Entity ID: {entity.get('id')}",
        f"URL: {entity_url(entity, host)}",
    ]
    for label, prop in (("File", "fileName"), ("Type", "mimeType"), ("Date", "date")):
        if props.get(prop):
            header.append(f"{label}: {_values(props[prop])}")

    if not chunks:
        header.append(
            "\nNo extracted text on this entity. Documents with pages store their "
            "text on child Page entities — use aleph_expand_entity with "
            "properties=['parent'] to list them, then fetch a page by its ID."
        )
        return "\n".join(header)

    text = "\n\n".join(chunks)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    header.append(f"Characters: {len(text)}" + (" (truncated)" if truncated else ""))
    return "\n".join(header) + "\n\n---\n\n" + text
