"""Formatter tests: compact output that keeps IDs, sources and provenance."""

from aleph_mcp import format as fmt


class TestEntitySummary:
    def test_includes_identifiers_and_featured_properties(self, person, schemata):
        out = fmt.entity_summary(person, schemata, "https://search.openaleph.org/", index=1)
        assert out.startswith("1. Ivan Petrov — Person")
        # 'nationality' and 'birthDate' are featured on Person; 'position' is not.
        assert "Nationality: ru" in out
        assert "Birth date: 1961-07-14" in out
        # "name" is inherited from Thing; its label must still resolve.
        assert "Name: Ivan Petrov" in out
        assert "position" not in out.lower() or "Director" not in out
        assert "Entity ID: e1" in out
        assert "URL: https://search.openaleph.org/entities/e1" in out
        assert "Dataset: Russian company registry (id c9)" in out

    def test_shows_highlight_snippets(self, person, schemata):
        out = fmt.entity_summary(person, schemata, "https://search.openaleph.org/")
        assert "Match: …director <em>Ivan Petrov</em> was appointed…" in out

    def test_falls_back_to_caption_properties(self, schemata):
        entity = {"id": "e3", "schema": "Person", "properties": {"name": ["Jane Roe"]}}
        assert "Jane Roe" in fmt.entity_summary(entity, schemata, "https://x/")

    def test_untitled_when_nothing_to_caption(self, schemata):
        entity = {"id": "e4", "schema": "Person", "properties": {}}
        assert "(untitled)" in fmt.entity_summary(entity, schemata, "https://x/")

    def test_builds_url_when_links_missing(self, schemata):
        entity = {"id": "e5", "schema": "Person", "properties": {"name": ["A"]}}
        out = fmt.entity_summary(entity, schemata, "https://search.openaleph.org/")
        assert "URL: https://search.openaleph.org/entities/e5" in out

    def test_survives_unknown_schema(self):
        entity = {"id": "e6", "schema": "Martian", "caption": "Zog", "properties": {}}
        out = fmt.entity_summary(entity, {}, "https://x/")
        assert "Zog — Martian" in out and "Entity ID: e6" in out


class TestValueRendering:
    def test_truncates_long_values(self, person, schemata):
        out = fmt.entity_detail(person, schemata, "https://x/")
        assert "…" in out
        assert "x" * 400 not in out

    def test_caps_number_of_values_and_counts_the_rest(self):
        assert fmt._values([str(i) for i in range(9)]).endswith("(+4 more)")

    def test_renders_nested_entity_values_by_caption(self):
        nested = {"id": "z", "caption": "Acme Ltd", "schema": "Company"}
        assert fmt._values([nested]) == "Acme Ltd [Company]"


class TestEntityDetail:
    def test_lists_all_properties_featured_first(self, person, schemata):
        out = fmt.entity_detail(person, schemata, "https://search.openaleph.org/")
        assert out.startswith("# Ivan Petrov")
        assert "A natural person" in out
        # 'position' is non-featured and must still appear.
        assert "Director" in out
        assert out.index("Birth date") < out.index("Director")
        assert "First seen: 2019-03-01T00:00:00" in out


class TestSearchResults:
    def test_renders_pagination_facets_and_hits(self, search_response, schemata):
        out = fmt.search_results(search_response, schemata, "https://search.openaleph.org/")
        assert "Showing 2 of 57 result(s), offset 0." in out
        assert "Use offset=2 for the next page." in out
        assert "1. Ivan Petrov — Person" in out
        assert "2. Northwind Trading Ltd — Company" in out
        assert "Countries [countries]: Russia (40), Cyprus (9)" in out
        # An empty facet block should not produce a stray heading.
        assert "Entity type" not in out

    def test_numbering_continues_across_pages(self, search_response, schemata):
        search_response["offset"] = 20
        out = fmt.search_results(search_response, schemata, "https://x/")
        assert "21. Ivan Petrov" in out and "22. Northwind" in out

    def test_empty_results_suggest_a_next_step(self, schemata):
        out = fmt.search_results({"total": 0, "results": []}, schemata, "https://x/")
        assert "No results." in out and "broader query" in out

    def test_handles_elasticsearch_style_total_object(self, search_response, schemata):
        search_response["total"] = {"value": 57, "relation": "eq"}
        out = fmt.search_results(search_response, schemata, "https://x/")
        assert "Showing 2 of 57 result(s)" in out


class TestExpand:
    def test_groups_by_relationship_property(self, person, schemata):
        data = {
            "total": 1,
            "results": [
                {"property": "directorshipDirector", "count": 3, "entities": [person]}
            ],
        }
        out = fmt.expand_results(data, schemata, "https://x/")
        assert "## directorshipDirector (3 total)" in out
        assert "1. Ivan Petrov — Person" in out

    def test_empty(self, schemata):
        assert "No relationships" in fmt.expand_results({"results": []}, schemata, "https://x/")


class TestSimilar:
    def test_flat_shape(self, person, schemata):
        data = {"total": 1, "results": [dict(person, score=9.5)]}
        out = fmt.similar_results(data, schemata, "https://x/")
        assert "Ivan Petrov" in out and "Score: 9.5" in out
        assert "not a confirmed identity match" in out

    def test_nested_entity_shape(self, person, schemata):
        data = {"total": 1, "results": [{"score": 3.0, "entity": person}]}
        out = fmt.similar_results(data, schemata, "https://x/")
        assert "Ivan Petrov" in out and "Score: 3.0" in out


class TestXref:
    def test_renders_both_sides_of_a_match(self, person, company, schemata):
        data = {
            "total": 1,
            "results": [
                {
                    "score": 7.1,
                    "decision": "undecided",
                    "entity": person,
                    "match": company,
                    "match_collection": {"id": "c4", "label": "Cyprus registry"},
                }
            ],
        }
        out = fmt.xref_results(data, schemata, "https://x/")
        assert "score 7.1 — decision: undecided" in out
        assert "Source: Ivan Petrov [Person] (e1)" in out
        assert "Match:  Northwind Trading Ltd [Company] (e2)" in out
        assert "Match dataset: Cyprus registry" in out

    def test_empty_explains_why(self, schemata):
        assert "none have been computed" in fmt.xref_results({"results": []}, schemata, "https://x/")


class TestCollections:
    def test_detail_includes_provenance_and_type_breakdown(self):
        collection = {
            "id": "c9",
            "label": "Cyprus registry",
            "foreign_id": "cy_registry",
            "category": "company",
            "countries": ["cy"],
            "publisher": "Registrar of Companies",
            "frequency": "monthly",
            "count": 412000,
            "summary": "Corporate filings.",
            "statistics": {"schema": {"values": [{"id": "Company", "label": "Company", "count": 400}]}},
            "links": {"ui": "https://search.openaleph.org/datasets/c9"},
        }
        out = fmt.collection_detail(collection, "https://search.openaleph.org/")
        assert "Publisher: Registrar of Companies" in out
        assert "Update frequency: monthly" in out
        assert "Entity count: 412000" in out
        assert "## Summary\nCorporate filings." in out
        assert "- Company: 400" in out

    def test_list_numbering_and_ids(self):
        data = {"total": 1, "offset": 0, "results": [{"id": "c1", "label": "Leak A", "category": "leak"}]}
        out = fmt.collection_results(data, "https://search.openaleph.org/")
        assert "1. Leak A" in out
        assert "Collection ID: c1" in out
        assert "URL: https://search.openaleph.org/datasets/c1" in out


class TestStatistics:
    def test_handles_faceted_and_mapping_shapes(self):
        data = {
            "collections": 300,
            "things": 1_000_000,
            "schemata": {"values": [{"id": "Person", "label": "People", "count": 5}]},
            "categories": {"leak": {"count": 12}},
        }
        out = fmt.statistics_summary(data)
        assert "Collections: 300" in out and "Entities: 1000000" in out
        assert "- People: 5" in out
        assert "- leak: 12" in out


class TestSchemaInfo:
    def test_index_separates_matchable(self, schemata):
        out = fmt.schema_info(schemata, None)
        assert f"{len(schemata)} followthemoney schemata" in out
        assert "Person" in out.split("## Other")[0]
        assert "Ownership" in out.split("## Other")[1]

    def test_detail_lists_properties_with_ranges(self, schemata):
        out = fmt.schema_info(schemata, "Ownership")
        assert "- owner (Owner) — type=entity, range=LegalEntity, matchable" in out

    def test_suggests_close_names(self, schemata):
        assert "Did you mean: Person?" in fmt.schema_info(schemata, "persn")
        assert "Did you mean: Person?" in fmt.schema_info(schemata, "person")

    def test_no_suggestion_for_nonsense(self, schemata):
        assert fmt.schema_info(schemata, "Xyzzy") == "Unknown schema 'Xyzzy'."


class TestDocumentText:
    def test_returns_body_text_with_header(self, schemata):
        entity = {
            "id": "d1",
            "schema": "Pages",
            "caption": "contract.pdf",
            "properties": {
                "fileName": ["contract.pdf"],
                "mimeType": ["application/pdf"],
                "bodyText": ["Clause 1. The parties agree."],
            },
        }
        out = fmt.document_text(entity, schemata, "https://x/", 20_000)
        assert "File: contract.pdf" in out
        assert "Clause 1. The parties agree." in out
        assert "(truncated)" not in out

    def test_truncates_and_says_so(self, schemata):
        entity = {"id": "d2", "schema": "Pages", "properties": {"bodyText": ["a" * 5000]}}
        out = fmt.document_text(entity, schemata, "https://x/", 100)
        assert "(truncated)" in out
        assert out.count("a") <= 120

    def test_missing_text_explains_the_page_child_pattern(self, schemata):
        entity = {"id": "d3", "schema": "Document", "properties": {"fileName": ["scan.pdf"]}}
        out = fmt.document_text(entity, schemata, "https://x/", 20_000)
        assert "No extracted text" in out
        assert "aleph_expand_entity" in out
