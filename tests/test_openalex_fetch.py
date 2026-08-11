from __future__ import annotations

import os
from unittest import mock

from gen_rpt.openalex_fetch import (
    _academic_queries,
    _dedupe_ranked,
    _work_is_relevant,
    collect_openalex_sources,
    reconstruct_abstract,
)
from gen_rpt.web_fetch import SourceDocument


def _inverted(text: str) -> dict[str, list[int]]:
    output: dict[str, list[int]] = {}
    for index, token in enumerate(text.split()):
        output.setdefault(token, []).append(index)
    return output


def test_reconstruct_abstract_preserves_word_order() -> None:
    assert reconstruct_abstract({"infrastructure": [2], "Gulf": [0], "data-centre": [1]}) == (
        "Gulf data-centre infrastructure"
    )


def test_openalex_is_optional_without_a_key() -> None:
    with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": ""}, clear=False):
        with mock.patch("gen_rpt.openalex_fetch.requests.get") as get:
            assert collect_openalex_sources("data-centre infrastructure", []) == []
    get.assert_not_called()


def test_topic_anchor_rejects_generic_cooling_paper() -> None:
    work = {
        "display_name": "Immersion cooling for lithium-ion batteries",
        "abstract_inverted_index": _inverted(
            "Cooling systems improve battery power performance and infrastructure reliability."
        ),
    }
    assert not _work_is_relevant(work, "ai data centre infrastructure power cooling network")


def test_academic_queries_prefer_focused_planner_terms_over_geography_variants() -> None:
    queries = _academic_queries(
        "Data-centre infrastructure and economics across China and Gulf markets, covering power availability and cooling",
        [
            "site:iea.org filetype:pdf data centres electricity demand China Middle East 2024 2030",
            "site:ewec.ae data centre generation capacity reserve margin tariffs",
        ],
    )
    assert queries[0].startswith("data centre infrastructure")
    assert "data centre electricity demand" in queries[1]
    assert not any("site" in query or "filetype" in query for query in queries)


def test_openalex_dedupes_versioned_dois_with_the_same_title() -> None:
    first = SourceDocument(
        title="A reproducible audit of data-centre electricity demand",
        url="https://doi.org/10.1000/version-1",
        query="data centre electricity demand",
        snippet="",
        content="Academic abstract " * 20,
        source_type="academic",
        metadata={"doi": "https://doi.org/10.1000/version-1", "academic": True},
    )
    second = SourceDocument(
        title="A reproducible audit of data-centre electricity demand",
        url="https://doi.org/10.1000/version-2",
        query="data centre electricity demand",
        snippet="",
        content="Academic abstract " * 20,
        source_type="academic",
        metadata={"doi": "https://doi.org/10.1000/version-2", "academic": True},
    )
    assert _dedupe_ranked([(10.0, first), (9.0, second)], 4) == [first]


def test_openalex_returns_ranked_academic_source_metadata() -> None:
    abstract = (
        "Data centre electricity demand depends on compute utilisation, cooling efficiency, grid carbon intensity, "
        "network architecture and climate conditions. Empirical comparisons across regions show that power availability, "
        "water constraints and server density materially affect infrastructure planning and operating performance."
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1000/data-centres",
                "display_name": "Data-centre infrastructure and regional power systems",
                "publication_year": 2025,
                "publication_date": "2025-06-01",
                "cited_by_count": 42,
                "abstract_inverted_index": _inverted(abstract),
                "authorships": [{"author": {"display_name": "Ada Example"}}],
                "primary_location": {"source": {"display_name": "Energy Systems Journal"}},
                "best_oa_location": None,
                "open_access": {"is_oa": True},
                "is_retracted": False,
            }
        ]
    }
    environment = {
        "OPENALEX_API_KEY": "test-key",
        "GATEX_OPENALEX_MAX_QUERIES": "1",
        "GATEX_OPENALEX_MAX_SOURCES": "3",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch("gen_rpt.openalex_fetch.requests.get", return_value=response) as get:
            sources = collect_openalex_sources("data-centre infrastructure and power systems", [])
    assert len(sources) == 1
    source = sources[0]
    assert source.source_type == "academic"
    assert source.url == "https://doi.org/10.1000/data-centres"
    assert source.metadata["academic"] is True
    assert source.metadata["cited_by_count"] == 42
    assert source.metadata["venue"] == "Energy Systems Journal"
    params = get.call_args.kwargs["params"]
    assert params["api_key"] == "test-key"
    assert "has_abstract:true" in params["filter"]
