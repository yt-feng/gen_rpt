from __future__ import annotations

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from gen_rpt.deepseek_client import DeepSeekClient, _completion_content, _response_content
from gen_rpt.research_quality import build_research_fact_pack
from gen_rpt.gatex_whitepaper_pipeline import (
    _authors,
    _architecture_prompt,
    _chart_label_issues,
    _citation_rows,
    _clean_editorial_evidence,
    _collect_research,
    _english_source_title,
    _editorial_source_excerpt,
    _fallback_queries,
    _FailoverEditorialClient,
    _generate_visuals,
    _architecture_issues,
    _exhibit_information_units,
    _meta_narration_issue,
    _normalize_exhibit_layout,
    _normalize_exhibit_panels,
    _page_composition_issues,
    _normalize_panel,
    _panel_renderability_issue,
    _paragraph_word_count,
    _payload_renderability_issues,
    _printable_content_overlap_issue,
    _publication_copy_issues,
    _publication_copy_projection,
    _sanitize_architecture_copy,
    _sanitize_chapter_copy,
    _sanitize_visual_brief,
    _reporting_period_issues,
    _sanitize_research_sources,
    _source_packet,
    _source_tier,
    _uniform_dark_region_issue,
    semantic_visual_quality_issues,
    visual_quality_issues,
)
from gen_rpt.web_fetch import SourceDocument


def test_gpt_model_uses_apimart_endpoint() -> None:
    with mock.patch.dict(os.environ, {"APIMART_API_KEY": "test-key"}, clear=False):
        client = DeepSeekClient(model="gpt-5.6-sol")
    assert client.api_key == "test-key"
    assert client.base_url == "https://api.apimart.ai/v1"


def test_deepseek_model_keeps_deepseek_endpoint() -> None:
    with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
        client = DeepSeekClient(model="deepseek-chat")
    assert client.api_key == "test-key"
    assert client.base_url == "https://api.deepseek.com/v1"


def test_deepseek_v4_pro_keeps_deepseek_endpoint() -> None:
    with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
        client = DeepSeekClient(model="deepseek-v4-pro")
    assert client.api_key == "test-key"
    assert client.base_url == "https://api.deepseek.com/v1"
    assert not client.use_apimart


def test_deepseek_v4_structured_writing_disables_hidden_thinking() -> None:
    response = mock.Mock()
    response.status_code = 200
    response.headers = {}
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"status":"ready"}'}}]
    }
    with mock.patch.dict(
        os.environ,
        {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_THINKING": "disabled"},
        clear=False,
    ):
        with mock.patch("gen_rpt.deepseek_client.requests.post", return_value=response) as post:
            client = DeepSeekClient(model="deepseek-v4-pro")
            assert client.chat_json([{"role": "user", "content": "Return JSON."}]) == {"status": "ready"}

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["thinking"] == {"type": "disabled"}


def test_deepseek_retries_empty_json_mode_without_response_format() -> None:
    empty = mock.Mock()
    empty.status_code = 200
    empty.headers = {}
    empty.raise_for_status.return_value = None
    empty.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": ""}}],
        "usage": {"completion_tokens": 0},
    }
    complete = mock.Mock()
    complete.status_code = 200
    complete.headers = {}
    complete.raise_for_status.return_value = None
    complete.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"status":"ready"}'}}]
    }
    environment = {
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_THINKING": "disabled",
        "DEEPSEEK_RETRY_ATTEMPTS": "2",
        "APIMART_RETRY_BASE_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=[empty, complete],
        ) as post:
            client = DeepSeekClient(model="deepseek-v4-pro")
            assert client.chat_json([{"role": "user", "content": "Return JSON."}]) == {"status": "ready"}

    assert post.call_args_list[0].kwargs["json"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in post.call_args_list[1].kwargs["json"]


def test_apimart_sol_uses_responses_pro_max_with_step_sized_budget() -> None:
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"status":"ready"}'}],
            }
        ]
    }
    response.raise_for_status.return_value = None
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_REASONING_EFFORT": "max",
        "APIMART_REASONING_MODE": "pro",
        "APIMART_MIN_OUTPUT_TOKENS": "16000",
        "APIMART_EXPLICIT_TOKEN_MULTIPLIER": "3",
        "APIMART_ALLOW_CHAT_FALLBACK": "false",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch("gen_rpt.deepseek_client.requests.post", return_value=response) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat_json(
                [{"role": "user", "content": "Return JSON."}],
                max_tokens=1_000,
            ) == {"status": "ready"}

    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url == "https://api.apimart.ai/v1/responses"
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["reasoning"] == {"effort": "max", "mode": "pro"}
    assert payload["max_output_tokens"] == 3_000
    assert payload["max_tokens"] == 3_000


def test_apimart_uses_global_floor_when_call_has_no_explicit_budget() -> None:
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {"output_text": "ready"}
    response.raise_for_status.return_value = None
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_MIN_OUTPUT_TOKENS": "16000",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch("gen_rpt.deepseek_client.requests.post", return_value=response) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat([{"role": "user", "content": "Return text."}]) == "ready"
    assert post.call_args.kwargs["json"]["max_tokens"] == 16_000


def test_responses_parser_accepts_apimart_wrapped_choices() -> None:
    response = mock.Mock()
    response.json.return_value = {
        "code": 200,
        "data": {"choices": [{"message": {"content": "GateX"}}]},
    }
    assert _response_content(response) == "GateX"


def test_responses_parser_rejects_truncated_apimart_choice() -> None:
    response = mock.Mock()
    response.json.return_value = {
        "code": 200,
        "data": {
            "choices": [
                {
                    "message": {"content": '{"partial":'},
                    "finish_reason": "length",
                }
            ],
            "usage": {"completion_tokens": 16_000},
        },
    }
    try:
        _response_content(response)
    except ValueError as exc:
        assert "exhausted its output budget" in str(exc)
    else:
        raise AssertionError("A length-truncated response must be rejected.")


def test_apimart_responses_increases_exhausted_output_budget() -> None:
    incomplete = mock.Mock()
    incomplete.status_code = 200
    incomplete.raise_for_status.return_value = None
    incomplete.json.return_value = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    complete = mock.Mock()
    complete.status_code = 200
    complete.raise_for_status.return_value = None
    complete.json.return_value = {"output_text": '{"status":"ready"}'}
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_REASONING_EFFORT": "xhigh",
        "APIMART_REASONING_MODE": "pro",
        "APIMART_MIN_OUTPUT_TOKENS": "24000",
        "APIMART_MAX_OUTPUT_TOKENS": "64000",
        "APIMART_EXPLICIT_TOKEN_MULTIPLIER": "3",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=[incomplete, complete],
        ) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat_json(
                [{"role": "user", "content": "Return JSON."}],
                max_tokens=1_000,
            ) == {"status": "ready"}

    assert post.call_args_list[0].kwargs["json"]["max_tokens"] == 3_000
    assert post.call_args_list[1].kwargs["json"]["max_tokens"] == 6_000


def test_apimart_responses_retries_transient_500() -> None:
    failed = mock.Mock()
    failed.status_code = 500
    failed.headers = {}
    failed.raise_for_status.side_effect = RuntimeError("upstream 500")
    complete = mock.Mock()
    complete.status_code = 200
    complete.headers = {}
    complete.raise_for_status.return_value = None
    complete.json.return_value = {"output_text": '{"status":"ready"}'}
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_RETRY_ATTEMPTS": "3",
        "APIMART_RETRY_BASE_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with (
            mock.patch("gen_rpt.deepseek_client.requests.post", side_effect=[failed, failed, complete]) as post,
            mock.patch("gen_rpt.deepseek_client.time.sleep") as sleep,
        ):
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat_json([{"role": "user", "content": "Return JSON."}]) == {"status": "ready"}
    assert post.call_count == 3
    assert sleep.call_count == 2


def test_apimart_responses_honours_retry_after_on_429() -> None:
    limited = mock.Mock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "7"}
    limited.raise_for_status.side_effect = RuntimeError("rate limited")
    complete = mock.Mock()
    complete.status_code = 200
    complete.headers = {}
    complete.raise_for_status.return_value = None
    complete.json.return_value = {"output_text": '{"status":"ready"}'}
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_RETRY_ATTEMPTS": "2",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with (
            mock.patch("gen_rpt.deepseek_client.requests.post", side_effect=[limited, complete]),
            mock.patch("gen_rpt.deepseek_client.time.sleep") as sleep,
        ):
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat_json([{"role": "user", "content": "Return JSON."}]) == {"status": "ready"}
    sleep.assert_called_once_with(7.0)


def test_apimart_responses_does_not_retry_authentication_error() -> None:
    denied = mock.Mock()
    denied.status_code = 401
    denied.headers = {}
    denied.raise_for_status.side_effect = RuntimeError("unauthorised")
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_RETRY_ATTEMPTS": "5",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with (
            mock.patch("gen_rpt.deepseek_client.requests.post", return_value=denied) as post,
            mock.patch("gen_rpt.deepseek_client.time.sleep") as sleep,
        ):
            client = DeepSeekClient(model="gpt-5.6-sol")
            try:
                client.chat_json([{"role": "user", "content": "Return JSON."}])
            except RuntimeError as exc:
                assert "unauthorised" in str(exc)
            else:
                raise AssertionError("Authentication failures must stop immediately.")
    assert post.call_count == 1
    sleep.assert_not_called()


def test_editorial_client_fails_over_once_and_keeps_using_backup() -> None:
    class StubClient:
        def __init__(self, model: str, responses: list[object]) -> None:
            self.model = model
            self.responses = list(responses)
            self.calls = 0

        def chat_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response  # type: ignore[return-value]

    primary = StubClient("gpt-5.6-sol", [RuntimeError("upstream 500")])
    fallback = StubClient("deepseek-chat", [{"stage": 1}, {"stage": 2}])
    client = _FailoverEditorialClient(primary, fallback)  # type: ignore[arg-type]
    assert client.chat_json([]) == {"stage": 1}
    assert client.chat_json([]) == {"stage": 2}
    assert primary.calls == 1
    assert fallback.calls == 2


def test_completion_parser_accepts_sse_fallback() -> None:
    response = mock.Mock()
    response.json.side_effect = ValueError("not json")
    response.text = 'data: {"choices":[{"delta":{"content":"Gate"}}]}\n\ndata: {"choices":[{"delta":{"content":"X"}}]}\n\ndata: [DONE]\n'
    assert _completion_content(response) == "GateX"


def test_deepseek_retries_reasoning_only_completion_with_larger_budget() -> None:
    exhausted = mock.Mock()
    exhausted.status_code = 200
    exhausted.headers = {}
    exhausted.raise_for_status.return_value = None
    exhausted.json.return_value = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "private reasoning"},
            }
        ],
        "usage": {
            "completion_tokens": 5_500,
            "completion_tokens_details": {"reasoning_tokens": 5_500},
        },
    }
    complete = mock.Mock()
    complete.status_code = 200
    complete.headers = {}
    complete.raise_for_status.return_value = None
    complete.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"status":"ready"}'}}],
        "usage": {"completion_tokens": 12},
    }
    environment = {
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_MAX_TOKENS": "16000",
        "DEEPSEEK_RETRY_ATTEMPTS": "3",
        "APIMART_RETRY_BASE_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=[exhausted, complete],
        ) as post:
            client = DeepSeekClient(model="deepseek-v4-pro")
            assert client.chat_json(
                [{"role": "user", "content": "Return JSON."}],
                max_tokens=5_500,
            ) == {"status": "ready"}

    assert post.call_args_list[0].kwargs["json"]["max_tokens"] == 5_500
    assert post.call_args_list[1].kwargs["json"]["max_tokens"] == 11_000


def test_source_packet_prefers_official_and_requires_https() -> None:
    sources = []
    for index in range(8):
        sources.append(
            {
                "title": f"Official source {index}",
                "url": f"https://example{index}.gov/report.pdf",
                "domain": f"example{index}.gov",
                "source_type": "pdf",
                "content": "Verified public evidence " * 40,
            }
        )
    rows, packet = _source_packet({"sources": sources})
    assert len(rows) == 8
    assert rows[0]["id"] == "S1"
    assert rows[0]["qualityTier"] == "PRIMARY"
    assert "https://example0.gov/report.pdf" in packet


def test_source_packet_rejects_social_and_prediction_market_sources() -> None:
    sources = [
        {
            "title": f"Official source {index}",
            "url": f"https://authority{index}.gov/report",
            "domain": f"authority{index}.gov",
            "source_type": "html",
            "content": "Verified public evidence " * 40,
        }
        for index in range(8)
    ]
    sources.extend(
        [
            {
                "title": "Prediction market",
                "url": "https://polymarket.com/event/example",
                "domain": "polymarket.com",
                "source_type": "html",
                "content": "Market odds " * 100,
            },
            {
                "title": "Social post",
                "url": "https://www.youtube.com/watch?v=example",
                "domain": "youtube.com",
                "source_type": "snippet",
                "content": "Video claim " * 100,
            },
        ]
    )
    rows, _ = _source_packet({"sources": sources})
    assert not any("polymarket" in row["domain"] or "youtube" in row["domain"] for row in rows)


def test_citation_rows_cap_prevents_footnote_only_overflow_page() -> None:
    source_map = {
        f"S{index}": {
            "title": f"Long underlying source title {index}",
            "domain": f"authority{index}.gov",
            "url": f"https://authority{index}.gov/long-publication-path",
        }
        for index in range(1, 6)
    }
    rows = _citation_rows(source_map, source_map)
    assert len(rows) == 4


def test_research_sources_are_sanitized_before_fact_extraction() -> None:
    rows = _sanitize_research_sources(
        [
            {
                "title": "Official capacity release",
                "url": "https://energy.gov.example/capacity",
                "domain": "energy.gov.example",
                "content": "Verified official capacity evidence. " * 20,
            },
            {
                "title": "Prediction market chatter",
                "url": "https://polymarket.com/event/capacity",
                "domain": "polymarket.com",
                "content": "Unverified market odds. " * 20,
            },
            {
                "title": "Duplicate official capacity release",
                "url": "https://energy.gov.example/capacity?utm_source=test",
                "domain": "energy.gov.example",
                "content": "Duplicate official capacity evidence. " * 20,
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0].domain == "energy.gov.example"


def test_collect_research_filters_blocked_sources_before_building_fact_pack() -> None:
    official = [
        SourceDocument(
            title=f"Official release {index}",
            url=f"https://authority{index}.gov/release",
            query="capacity",
            snippet="Verified evidence",
            content="Verified official evidence " * 40,
            domain=f"authority{index}.gov",
        )
        for index in range(4)
    ]
    secondary = [
        SourceDocument(
            title=f"Industry source {index}",
            url=f"https://industry{index}.example/report",
            query="capacity",
            snippet="Corroborating evidence",
            content="Corroborating industry evidence " * 40,
            domain=f"industry{index}.example",
        )
        for index in range(8)
    ]
    blocked = SourceDocument(
        title="Prediction market",
        url="https://polymarket.com/event/capacity",
        query="capacity",
        snippet="Odds",
        content="Unverified odds " * 40,
        domain="polymarket.com",
    )
    fact_pack = SimpleNamespace(
        authoritative_source_count=4,
        to_dict=lambda: {"authoritative_source_count": 4},
    )
    planner = mock.Mock()
    planner.chat_json.return_value = {"queries": [f"evidence query {index}" for index in range(10)]}
    with TemporaryDirectory() as directory:
        with (
            mock.patch("gen_rpt.gatex_whitepaper_pipeline.DeepSeekClient", return_value=planner),
            mock.patch("gen_rpt.gatex_whitepaper_pipeline.collect_sources", return_value=[*secondary, blocked, *official]),
            mock.patch("gen_rpt.gatex_whitepaper_pipeline.collect_openalex_sources", return_value=[]),
            mock.patch("gen_rpt.gatex_whitepaper_pipeline.build_research_fact_pack", return_value=fact_pack) as build,
            mock.patch(
                "gen_rpt.gatex_whitepaper_pipeline.build_evidence_ledger",
                return_value=[{"id": f"E{index}"} for index in range(12)],
            ),
        ):
            result = _collect_research("Data-centre infrastructure", "Evidence-led brief", Path(directory))
    fact_sources = build.call_args.args[2]
    assert len(fact_sources) == 12
    assert not any("polymarket" in source.domain for source in fact_sources)
    assert not any("polymarket" in row["domain"] for row in result["sources"])


def test_academic_sources_are_context_not_authoritative_evidence() -> None:
    academic = {
        "title": "A peer-reviewed infrastructure study",
        "url": "https://doi.org/10.1000/example",
        "domain": "doi.org",
        "source_type": "academic",
        "content": "Empirical context " * 40,
        "metadata": {"academic": True},
    }
    assert _source_tier(academic) == "ACADEMIC"


def test_first_party_technical_sources_are_primary_evidence() -> None:
    domains = ("ultraethernet.org", "ashrae.org", "nvidia.com", "intel.com")
    sources = [
        SourceDocument(
            title=f"Official technical specification {index}",
            url=f"https://{domain}/technical-report-{index}.pdf",
            query="official technical specification",
            snippet="Documented performance, power and operating specifications.",
            content="Documented performance, power and operating specifications. " * 20,
            source_type="pdf",
            domain=domain,
        )
        for index, domain in enumerate(domains, start=1)
    ]
    fact_pack = build_research_fact_pack(
        "AI hardware systems",
        {"objective": "AI hardware systems", "decision_question": "What is documented?"},
        sources,
    )
    assert fact_pack.authoritative_source_count == 4
    assert all(_source_tier(source.__dict__) == "PRIMARY" for source in sources)


def test_source_packet_does_not_let_academic_sources_replace_primary_sources() -> None:
    sources = [
        {
            "title": f"Official source {index}",
            "url": f"https://authority{index}.gov/report",
            "domain": f"authority{index}.gov",
            "content": "Verified public evidence " * 40,
        }
        for index in range(3)
    ]
    sources.extend(
        {
            "title": f"Academic study {index}",
            "url": f"https://doi.org/10.1000/study-{index}",
            "domain": "doi.org",
            "source_type": "academic",
            "content": "Academic context " * 40,
            "metadata": {"academic": True},
        }
        for index in range(8)
    )
    sources.append(
        {
            "title": "Trade publication",
            "url": "https://industry.example.com/article",
            "domain": "industry.example.com",
            "content": "Secondary context " * 40,
        }
    )
    try:
        _source_packet({"sources": sources})
    except Exception as exc:
        assert "at least four primary or institutional" in str(exc)
    else:
        raise AssertionError("Academic sources must not satisfy the authoritative-source gate.")


def test_fallback_queries_follow_the_requested_topic() -> None:
    queries = _fallback_queries("UAE energy ecosystem investment outlook")
    assert len(queries) >= 10
    assert all("UAE energy ecosystem investment outlook" in query for query in queries)
    assert not any("STAR Market" in query or "China industrial robotics" in query for query in queries)


def test_technical_fallback_queries_start_with_primary_technical_evidence() -> None:
    queries = _fallback_queries("AI hardware systems and optical interconnects")
    assert "official technical report" in queries[0]
    assert "standards body" in queries[1]


def test_long_technical_topic_is_condensed_before_search() -> None:
    topic = (
        "AI software and model optimisation through 11 August 2026, covering quantisation, distillation, sparsity, "
        "mixture-of-experts routing, inference serving, compilers, memory management and workload economics."
    )
    queries = _fallback_queries(topic)
    assert all("through 11 August" not in query for query in queries)
    assert all(len(query) < 260 for query in queries)


def test_black_image_is_rejected() -> None:
    image = Image.new("RGB", (1280, 854), "black")
    issues = visual_quality_issues(image)
    assert any("near-black" in issue for issue in issues)


def test_large_black_band_is_rejected() -> None:
    image = Image.new("RGB", (1280, 854), "white")
    for y in range(300):
        for x in range(1280):
            image.putpixel((x, y), (0, 0, 0))
    issues = visual_quality_issues(image)
    assert "image contains a large solid-black band" in issues


def _sample_visual_bytes() -> bytes:
    image = Image.effect_noise((1280, 854), 48).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84)
    return buffer.getvalue()


def test_semantic_visual_qa_rejects_readable_generated_text() -> None:
    response = mock.Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "pass": False,
                            "readableTextPresent": True,
                            "readableText": ["A deep dive into consumer behavior"],
                            "contextRelevance": 0.9,
                            "professionalQuality": 0.8,
                            "issues": ["Prominent generated headline"],
                            "scene": "Retail storefront",
                        }
                    )
                }
            }
        ]
    }
    response.raise_for_status.return_value = None
    with mock.patch.dict(os.environ, {"QWEN_VL_API_KEY": "test-key"}, clear=False):
        with mock.patch("gen_rpt.gatex_whitepaper_pipeline.requests.post", return_value=response):
            issues = semantic_visual_quality_issues(
                _sample_visual_bytes(),
                brief="Chinese household consumption and retail activity",
            )
    assert any("readable" in issue for issue in issues)


def test_semantic_visual_qa_accepts_relevant_documentary_image() -> None:
    response = mock.Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "pass": True,
                            "readableTextPresent": False,
                            "readableText": [],
                            "contextRelevance": 0.94,
                            "professionalQuality": 0.91,
                            "issues": [],
                            "scene": "Automated semiconductor production line",
                        }
                    )
                }
            }
        ]
    }
    response.raise_for_status.return_value = None
    with mock.patch.dict(os.environ, {"QWEN_VL_API_KEY": "test-key"}, clear=False):
        with mock.patch("gen_rpt.gatex_whitepaper_pipeline.requests.post", return_value=response):
            issues = semantic_visual_quality_issues(
                _sample_visual_bytes(),
                brief="Semiconductor manufacturing equipment inside a cleanroom",
            )
    assert issues == []


def test_rendered_page_black_rectangle_is_rejected() -> None:
    image = Image.new("RGB", (840, 1188), "white")
    for y in range(320, 620):
        for x in range(80, 760):
            image.putpixel((x, y), (0, 0, 0))
    assert "solid near-black rendered region" in _uniform_dark_region_issue(image)


def test_normal_text_page_does_not_trigger_black_rectangle_check() -> None:
    image = Image.new("RGB", (840, 1188), "white")
    for y in range(160, 980, 28):
        for x in range(90, 700):
            if (x + y) % 13 == 0:
                image.putpixel((x, y), (20, 38, 68))
    assert _uniform_dark_region_issue(image) == ""


def test_text_crossing_printable_footer_boundary_is_rejected() -> None:
    issue = _printable_content_overlap_issue(
        page_height=841.92,
        y0=788.4,
        y1=794.7,
        text="3. OECD Economic Outlook",
    )
    assert "printable footer boundary" in issue


def test_page_furniture_below_printable_area_is_allowed() -> None:
    assert (
        _printable_content_overlap_issue(
            page_height=841.92,
            y0=809.6,
            y1=819.0,
            text="MEMBER CONFIDENTIAL GATEX.FUND 02 / 18",
        )
        == ""
    )


def test_author_emails_are_client_ready_and_deterministic() -> None:
    first = _authors("red-chips")
    second = _authors("red-chips")
    assert first == second
    assert first[0] == {"name": "Frank Feng", "role": "Managing Partner", "email": "frank@gatex.fund"}
    assert all(row["email"].endswith("@gatex.fund") and not row["email"].startswith("xxx") for row in first)


def test_outlook_word_count_excludes_metadata() -> None:
    outlook = {
        "title": "A long closing title that should not count",
        "deck": "Deck metadata is laid out separately from the body.",
        "callout": "This is also not paragraph prose.",
        "sourceIds": ["S1", "S2"],
        "paragraphs": ["One two three.", "Four five six."],
    }
    assert _paragraph_word_count(outlook) == 6


def test_executive_word_count_excludes_headline_deck_and_sources() -> None:
    executive = {
        "headline": "A metadata headline with several words",
        "deck": "A separate deck also contains words that do not occupy the body columns.",
        "sourceIds": ["S1", "S2"],
        "paragraphs": ["One two three.", "Four five six.", "Seven eight nine.", "Ten eleven twelve."],
    }
    assert _paragraph_word_count(executive) == 12


def test_valid_cached_visual_is_reused_without_api_call() -> None:
    with TemporaryDirectory() as directory:
        target_dir = Path(directory)
        image = Image.effect_noise((1280, 854), 48).convert("RGB")
        image.save(target_dir / "chapter-1.jpg", format="JPEG", quality=90)
        content = {"visuals": [{"id": "chapter-1", "prompt": "Semiconductor production", "alt": "Production line"}]}
        with mock.patch("gen_rpt.gatex_whitepaper_pipeline._download_apimart_image") as download:
            result = _generate_visuals(content, target_dir)
        download.assert_not_called()
        assert result["chapter-1"]["path"].endswith("chapter-1.jpg")


def test_visual_brief_removes_text_bearing_display_instructions() -> None:
    brief = _sanitize_visual_brief(
        "A photograph of the King Abdullah Financial District in Riyadh, with modern skyscrapers, "
        "a digital billboard displaying Arabic script, no text.",
        fallback="King Abdullah Financial District skyline in Riyadh.",
    )

    assert "King Abdullah Financial District" in brief
    assert "modern skyscrapers" in brief
    assert "billboard" not in brief.lower()
    assert "script" not in brief.lower()


def test_chinese_source_titles_are_rendered_as_english_citations() -> None:
    prospectus = {
        "title": "[PDF] 首次公开发行股票并在科创板上市招股说明书（注册稿）",
        "domain": "static.sse.com.cn",
    }
    company = {"title": "长鑫科技集团股份有限公司", "domain": "static.sse.com.cn"}
    assert _english_source_title(prospectus) == "STAR Market Initial Public Offering Prospectus (Registration Draft)"
    assert _english_source_title(company) == "ChangXin Memory Technologies Group Co., Ltd. Filing"


def test_dangling_search_result_ellipsis_is_removed_from_citation_title() -> None:
    source = {
        "title": "China's retail sales rose in the first half of ...",
        "domain": "english.www.gov.cn",
    }
    assert _english_source_title(source) == "China's retail sales rose in the first half"


def test_source_title_dashes_are_normalized_for_pdf_typography() -> None:
    source = {
        "title": "Electricity Mid-Year Update 2025 \u2013 Analysis \u2014 IEA",
        "domain": "iea.org",
    }
    assert _english_source_title(source) == "Electricity Mid-Year Update 2025 - Analysis - IEA"


def test_malformed_comparison_panel_falls_back_to_populated_matrix() -> None:
    panel = {
        "type": "comparison",
        "columns": ["A", "B"],
        "items": [
            {"tag": "Foundry", "title": "Audited operating record", "body": "Listed-company evidence."},
            {"tag": "Memory", "title": "Prospectus record", "body": "Capacity and research evidence."},
            {"tag": "Cloud", "title": "Risk architecture", "body": "Infrastructure disclosure."},
            {"tag": "Robotics", "title": "Deployment record", "body": "Operating and order evidence."},
        ],
    }
    normalized = _normalize_panel(panel)
    assert normalized["type"] == "matrix"
    assert len(normalized["items"]) == 4
    assert _panel_renderability_issue(normalized) == ""


def test_complete_compact_charts_are_renderable() -> None:
    assert _panel_renderability_issue(
        {
            "type": "line",
            "xLabels": ["2024", "2025E", "2026E"],
            "series": [{"name": "Real GDP", "values": [2.0, 3.6, 3.9]}],
        }
    ) == ""
    assert _panel_renderability_issue(
        {
            "type": "bars",
            "items": [
                {"label": "Revenue", "value": 305.9},
                {"label": "Expenditure", "value": 350.1},
                {"label": "Deficit", "value": 44.0},
            ],
        }
    ) == ""
    assert _panel_renderability_issue(
        {
            "type": "stacked_bar",
            "items": [
                {
                    "label": "Sales mix",
                    "segments": [
                        {"label": "Battery electric", "value": 67},
                        {"label": "Other", "value": 33},
                    ],
                }
            ],
        }
    ) == ""
    assert _panel_renderability_issue(
        {
            "type": "vehicle_scale",
            "items": [
                {"label": "A", "height": 70, "diameter": 3.7, "payload": "22.8 t"},
                {"label": "B", "height": 124.4, "diameter": 9, "payload": ">100 t"},
                {"label": "C", "height": 114, "diameter": 10.6, "payload": "50 t"},
            ],
        }
    ) == ""


def test_two_by_two_comparison_counts_four_observations() -> None:
    exhibit = {
        "metrics": [{"value": "4.3%"}, {"value": "5.4%"}],
        "panels": [
            {
                "type": "comparison",
                "columns": ["Earlier", "Latest"],
                "items": [
                    {"metric": "GDP", "left": "5.0%", "right": "4.3%"},
                    {"metric": "Industry", "left": "6.1%", "right": "5.4%"},
                ],
            }
        ],
    }
    assert _exhibit_information_units(exhibit) == 6
    assert _panel_renderability_issue(exhibit["panels"][0]) == ""


def test_two_panel_exhibit_is_trimmed_to_two_metric_cards() -> None:
    exhibit = {
        "metrics": [{"value": str(index), "label": f"Metric {index}"} for index in range(4)],
        "panels": [
            {
                "type": "matrix",
                "items": [
                    {"tag": str(index), "title": f"Layer {index}", "body": "Grounded operating evidence."}
                    for index in range(4)
                ],
            },
            {
                "type": "scenario",
                "items": [
                    {"label": f"Case {index}", "range": "Bounded", "body": "Documented condition."}
                    for index in range(3)
                ],
            },
        ],
    }

    normalized = _normalize_exhibit_layout(exhibit)

    assert len(normalized["panels"]) == 2
    assert len(normalized["metrics"]) == 2


def test_meta_narration_is_rejected_from_editorial_copy() -> None:
    assert "meta narration" in _meta_narration_issue("The opening chapter establishes the demand backdrop.")
    assert "meta narration" in _meta_narration_issue("Industrial activity and investment are examined together.")
    assert _meta_narration_issue("Industrial output remained firmer than household demand.") == ""


def test_non_usd_currency_is_rejected_before_final_assembly() -> None:
    issues = _publication_copy_issues(
        "Retail sales reached 60 trillion yuan (approximately $8.8 trillion) by 2030."
    )
    assert any("non-USD currency" in issue for issue in issues)
    assert _publication_copy_issues("Retail sales may reach approximately $8.8 trillion by 2030.") == []


def test_model_names_are_allowed_as_subjects_but_not_as_production_disclosure() -> None:
    assert _publication_copy_issues("DeepSeek and Qwen expanded their model families.") == []
    assert any(
        "Production-tool disclosure" in issue
        for issue in _publication_copy_issues("This publication was generated using DeepSeek.")
    )


def test_internal_visual_prompt_is_not_treated_as_publication_copy() -> None:
    projected = _publication_copy_projection(
        {
            "coverSummary": "Capacity expanded with operating demand.",
            "visuals": [
                {"id": "chapter-1", "prompt": "Photograph for this report", "alt": "Operating facility"}
            ],
        }
    )

    assert "prompt" not in projected["visuals"][0]
    assert _publication_copy_issues(projected) == []


def test_architecture_sanitizer_replaces_cover_meta_narration_with_substantive_decks() -> None:
    architecture = {
        "coverSummary": (
            "Verified capacity expanded across the supply chain. "
            "This report examines the operating evidence and market outlook."
        ),
        "executiveSummary": {
            "headline": "Deployment economics set the pace",
            "deck": "Power, integration and customer adoption determine whether technical capacity becomes productive output.",
        },
        "chapters": [
            {
                "deck": "Supplier depth improves delivery resilience across multiple operating environments.",
                "callout": "Documented demand remains concentrated in projects with funded infrastructure.",
            },
            {
                "deck": "Commercial execution depends on qualified talent, stable utilities and repeat customer demand.",
                "callout": "Operating proof matters more than announced capacity or broad market ambition.",
            },
        ],
    }

    cleaned = _sanitize_architecture_copy(architecture)

    assert "This report" not in cleaned["coverSummary"]
    assert 55 <= len(cleaned["coverSummary"].split()) <= 80
    assert "productive output" in cleaned["coverSummary"]


def test_chapter_sanitizer_removes_meta_narration_and_trims_complete_sentences() -> None:
    paragraph = " ".join(
        [
            "This chapter explains the production system in broad terms.",
            *[f"Operating sentence {index} contains specific evidence about capacity and delivery." for index in range(1, 15)],
        ]
    )
    chapter = {
        "opening": paragraph,
        "subsections": [
            {"heading": f"Layer {index}", "paragraphs": [paragraph, paragraph]}
            for index in range(1, 5)
        ],
    }

    cleaned = _sanitize_chapter_copy(chapter)

    assert "This chapter" not in cleaned["opening"]
    assert len(cleaned["opening"].split()) <= 120
    assert all(len(text.split()) <= 88 for section in cleaned["subsections"] for text in section["paragraphs"])
    assert all(text.endswith(".") for section in cleaned["subsections"] for text in section["paragraphs"])


def test_editorial_source_excerpt_hides_non_usd_amounts_but_keeps_operating_metrics() -> None:
    excerpt = _editorial_source_excerpt(
        "Revenue declined 25.2% to RMB1,464.3 million while exports reached US$79 million.",
        500,
    )

    assert "25.2%" in excerpt
    assert "RMB" not in excerpt
    assert "US$79 million" in excerpt


def test_editorial_evidence_removes_addresses_table_headers_and_non_usd_money() -> None:
    rows = [
        {"fact": "PRINCIPAL PLACE OF BUSINESS Room 1917, 19/F Lee Garden One", "value": 2022, "unit": "$", "display_value": "$2022"},
        {"fact": "Revenue 2025 2024 USD'000 USD'000", "value": 2024, "unit": "$", "display_value": "$2024"},
        {"fact": "Revenue reached RMB1,198 million in 2025.", "value": 1198, "unit": "$M", "display_value": "$1198M"},
        {"fact": "International revenue exceeded 70 percent in 2025.", "value": 70, "unit": "%", "display_value": "70%"},
        {"fact": "Revenue reached US$79 million in 2025.", "value": 79, "unit": "$M", "display_value": "$79M"},
    ]

    cleaned = _clean_editorial_evidence(rows)

    assert [row["display_value"] for row in cleaned] == ["70%", "$79M"]


def test_unfinished_quarter_requires_an_explicit_data_boundary() -> None:
    issues = _reporting_period_issues(
        "China Economics Quarterly: Q3 2026",
        "2026-08-10",
        {"coverSummary": "Q3 2026 grew by 4.8% as demand recovered."},
    )
    assert any("latest-data or outlook boundary" in issue for issue in issues)
    assert any("finalized result" in issue for issue in issues)


def test_unfinished_quarter_accepts_entering_quarter_language() -> None:
    assert _reporting_period_issues(
        "China Economics Quarterly: Q3 2026",
        "2026-08-10",
        {
            "coverSummary": (
                "Entering Q3 2026, the latest available data through H1 show mixed demand. "
                "The Q3 outlook remains conditional."
            )
        },
    ) == []


def test_architecture_requires_dense_exhibits() -> None:
    panels = [
        {
            "type": "matrix",
            "items": [{"title": f"Signal {item}", "body": "Evidence"} for item in range(4)],
        },
        {
            "type": "bars",
            "items": [{"label": f"Segment {item}", "value": item + 1} for item in range(4)],
        },
        {
            "type": "comparison",
            "columns": ["Earlier", "Latest"],
            "items": [
                {"metric": f"Measure {item}", "left": str(item), "right": str(item + 1)}
                for item in range(4)
            ],
        },
        {
            "type": "process",
            "items": [{"title": f"Stage {item}", "body": "Evidence"} for item in range(4)],
        },
    ]
    architecture = {
        "executiveSummary": {"headline": "Macro momentum slows", "deck": "Demand trails production."},
        "chapters": [
            {"title": f"Chapter finding {index}", "deck": "A substantive finding", "callout": "Bounded evidence"}
            for index in range(4)
        ],
        "exhibits": [
            {
                "metrics": [{"value": "1"}, {"value": "2"}],
                "panels": [panels[index]],
            }
            for index in range(4)
        ],
        "outlook": {"title": "Outlook", "deck": "Conditions remain mixed."},
        "visuals": [{"id": f"visual-{index}"} for index in range(5)],
    }
    assert _architecture_issues(architecture) == []


def test_architecture_rejects_repetitive_exhibit_grammar() -> None:
    panel = {
        "type": "matrix",
        "items": [{"title": f"Signal {item}", "body": "Evidence"} for item in range(4)],
    }
    architecture = {
        "executiveSummary": {"headline": "Macro momentum slows", "deck": "Demand trails production."},
        "chapters": [
            {"title": f"Finding {index}", "deck": "A substantive finding", "callout": "Bounded evidence"}
            for index in range(4)
        ],
        "exhibits": [
            {"metrics": [{"value": "1"}, {"value": "2"}], "panels": [panel]}
            for _ in range(4)
        ],
        "outlook": {"title": "Outlook", "deck": "Conditions remain mixed."},
        "visuals": [{"id": f"visual-{index}"} for index in range(5)],
    }
    assert any("Panel types repeated" in issue for issue in _architecture_issues(architecture))


def test_chart_label_gate_detects_clipped_text() -> None:
    exhibit = {
        "panels": [
            {
                "type": "bars",
                "items": [
                    {"label": "Electricity, heat, gas and water supply", "value": 4.3},
                    {"label": "Manufacturing", "value": 6.4},
                    {"label": "Mining", "value": 6.0},
                    {"label": "Total industry", "value": 6.1},
                ],
            }
        ]
    }
    issues = _chart_label_issues(exhibit, "ctricity, heat, gas and water supply Manufacturing Mining Total industry")
    assert issues == ["Rendered chart label is missing or clipped: Electricity, heat, gas and water supply"]


def test_page_composition_gate_rejects_orphaned_source_notes() -> None:
    text = (
        "GATEX | EXECUTIVE INTELLIGENCE\nSOURCES AND NOTES\n"
        "1. Red Sea attacks increased shipping times and freight rates. https://eia.gov\n"
        "2. IMF Direction of Trade Statistics. https://data.imf.org\n"
    )

    assert _page_composition_issues(text, 10) == [
        "Page 10 is unexpectedly sparse.",
        "Page 10 contains orphaned source notes.",
    ]


def test_page_composition_gate_allows_sources_on_an_exhibit_page() -> None:
    text = (
        "EXHIBIT 2\nTrade Scale Meets Route Exposure\n"
        + "Documented bilateral trade and freight evidence. " * 25
        + "\nSOURCES AND NOTES\n1. IMF Direction of Trade Statistics. https://data.imf.org"
    )

    assert _page_composition_issues(text, 9) == []


def test_compact_architecture_retry_reduces_evidence_payload() -> None:
    sources = [
        {
            "id": f"S{index}",
            "title": f"Technical source {index}",
            "url": f"https://example.com/{index}",
            "excerpt": "Documented model, benchmark and deployment evidence. " * 80,
        }
        for index in range(20)
    ]
    evidence = [
        {"claim": "Documented model and deployment evidence. " * 30, "sourceIds": [f"S{index + 1}"]}
        for index in range(18)
    ]
    regular = _architecture_prompt(
        title="AI and Large Language Models",
        topic="China capability and Gulf deployment economics",
        brief="Evidence-led GateX report.",
        sources=sources,
        evidence=evidence,
    )
    compact = _architecture_prompt(
        title="AI and Large Language Models",
        topic="China capability and Gulf deployment economics",
        brief="Evidence-led GateX report.",
        sources=sources,
        evidence=evidence,
        compact=True,
    )

    assert len(compact) < len(regular) * 0.8


def test_architecture_output_budget_stays_within_apimart_context_window() -> None:
    source = Path("gen_rpt/gatex_whitepaper_pipeline.py").read_text(encoding="utf-8")

    assert "(2_000 if attempt > 0 else 2_200)" in source
    assert "(4_000 if attempt > 0 else 5_500)" in source


def test_incomplete_optional_exhibit_panel_is_dropped() -> None:
    panels = [
        {
            "type": "matrix",
            "items": [
                {"title": "Demand", "body": "Household demand remains selective."},
                {"title": "Industry", "body": "Industrial output is comparatively resilient."},
                {"title": "Property", "body": "Property continues to weigh on confidence."},
                {"title": "Trade", "body": "External demand remains uneven."},
            ],
        },
        {
            "type": "scenario",
            "items": [
                {"label": "Base", "range": "4.5-5.0%", "body": "Gradual stabilisation."},
                {"label": "Downside", "range": "Below 4.5%", "body": "Property drag persists."},
            ],
        },
    ]
    normalized = _normalize_exhibit_panels(panels)
    assert len(normalized) == 1
    assert normalized[0]["type"] == "matrix"


def test_empty_exhibit_panel_fails_release_renderability_gate() -> None:
    payload = {
        "contentSections": [
            {
                "kind": "exhibit",
                "exhibit": {
                    "metrics": [{"value": "15.52%", "label": "R&D intensity"}],
                    "panels": [
                        {
                            "type": "comparison",
                            "columns": ["A", "B"],
                            "items": [],
                        }
                    ],
                },
            }
        ]
    }
    issues = _payload_renderability_issues(payload)
    assert any("Expected four rendered exhibits" in issue for issue in issues)
    assert any("comparison requires" in issue for issue in issues)
