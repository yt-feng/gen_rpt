from __future__ import annotations

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import pytest
import fitz
import requests
from PIL import Image

from gen_rpt.deepseek_client import (
    DeepSeekClient,
    EditorialServiceExhausted,
    _completion_content,
    _response_content,
)
from gen_rpt.research_quality import build_research_fact_pack
from gen_rpt.web_evidence import build_evidence_ledger
from gen_rpt.gatex_whitepaper_pipeline import (
    _architecture_prompt,
    _build_payload,
    _chart_label_issues,
    _citation_rows,
    _claim_identity_tokens,
    _clean_editorial_evidence,
    _collect_research,
    _complete_exhibit_information_units,
    _complete_sparse_exhibit,
    _english_source_title,
    _editorial_source_excerpt,
    _editorial_issues,
    _fallback_queries,
    _merge_named_region_evidence,
    _regional_anchor_queries,
    _required_source_regions,
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
    _sanitize_editorial_paragraphs,
    _source_is_topic_contamination,
    _source_is_regional_anchor,
    _source_matches_region,
    _source_packet,
    _source_tier,
    _source_supports_claim,
    _numeric_claim_issues,
    _exhibit_subject_issues,
    _uniform_dark_region_issue,
    semantic_visual_quality_issues,
    visual_quality_issues,
)
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_fetch import _direct_source_candidates, _extract_pdf_text


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


def test_explicit_deepseek_provider_ignores_apimart_force_route() -> None:
    environment = {
        "APIMART_FORCE_CHAT": "true",
        "DEEPSEEK_API_KEY": "deepseek-test-key",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        client = DeepSeekClient(model="deepseek-chat", provider="deepseek")

    assert client.use_apimart is False
    assert client.api_key == "deepseek-test-key"
    assert client.base_url == "https://api.deepseek.com/v1"


@pytest.mark.parametrize("status_code", [500, 521])
def test_retryable_apimart_exhaustion_is_typed_for_bounded_failover(
    status_code: int,
) -> None:
    unavailable = mock.Mock()
    unavailable.status_code = status_code
    unavailable.headers = {}
    unavailable.raise_for_status.side_effect = requests.HTTPError(
        "500 upstream body must not become a route log"
    )
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_RETRY_ATTEMPTS": "2",
        "APIMART_RETRY_BASE_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=unavailable,
        ) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            with pytest.raises(EditorialServiceExhausted) as exc_info:
                client.chat_json([{"role": "user", "content": "Return JSON."}])

    assert post.call_count == 2
    assert exc_info.value.failure_kind == "retryable_http"
    assert exc_info.value.status_code == status_code
    assert "upstream body" not in str(exc_info.value)


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


def test_apimart_responses_strict_budget_bypasses_multiplier_floor_and_growth() -> None:
    incomplete = mock.Mock()
    incomplete.status_code = 200
    incomplete.headers = {}
    incomplete.raise_for_status.return_value = None
    incomplete.json.return_value = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_MIN_OUTPUT_TOKENS": "24000",
        "APIMART_EXPLICIT_MIN_OUTPUT_TOKENS": "24000",
        "APIMART_EXPLICIT_TOKEN_MULTIPLIER": "3",
        "APIMART_MAX_OUTPUT_TOKENS": "64000",
        "APIMART_RETRY_ATTEMPTS": "4",
        "APIMART_RETRY_BASE_SECONDS": "0",
        "APIMART_ALLOW_CHAT_FALLBACK": "true",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=incomplete,
        ) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            with pytest.raises(EditorialServiceExhausted) as exc_info:
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    fallback_max_tokens=6_000,
                    strict_output_budget=True,
                )

    assert exc_info.value.failure_kind == "output_budget"
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 8_000
    assert payload["max_output_tokens"] == 8_000


def test_apimart_strict_json_rejects_invalid_payload_without_model_repair() -> None:
    invalid = mock.Mock()
    invalid.status_code = 200
    invalid.headers = {}
    invalid.raise_for_status.return_value = None
    invalid.json.return_value = {"output_text": '{"title":"partial",}'}
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "true",
        "APIMART_ALLOW_CHAT_FALLBACK": "true",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=invalid,
        ) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            with pytest.raises(ValueError, match="strict output contract"):
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    strict_output_budget=True,
                )

    assert post.call_count == 1


def test_apimart_chat_strict_budget_is_exact_and_accepts_shared_route_kwargs() -> None:
    complete = mock.Mock()
    complete.status_code = 200
    complete.headers = {}
    complete.raise_for_status.return_value = None
    complete.json.return_value = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"status":"ready"}'},
            }
        ]
    }
    environment = {
        "APIMART_API_KEY": "test-key",
        "APIMART_USE_RESPONSES": "false",
        "APIMART_MIN_OUTPUT_TOKENS": "24000",
        "APIMART_EXPLICIT_MIN_OUTPUT_TOKENS": "24000",
        "APIMART_EXPLICIT_TOKEN_MULTIPLIER": "3",
        "BACKEND_URL": "",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=complete,
        ) as post:
            client = DeepSeekClient(model="gpt-5.6-sol")
            assert client.chat_json(
                [{"role": "user", "content": "Return JSON."}],
                max_tokens=8_000,
                fallback_max_tokens=6_000,
                strict_output_budget=True,
            ) == {"status": "ready"}

    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 8_000
    assert "max_output_tokens" not in payload


def test_backend_truncation_check_is_strict_only() -> None:
    truncated = mock.Mock()
    truncated.status_code = 200
    truncated.raise_for_status.return_value = None
    truncated.json.return_value = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "legacy partial content"},
            }
        ]
    }
    environment = {
        "BACKEND_URL": "https://backend.example",
        "INTERNAL_TOKEN": "test-token",
        "DEEPSEEK_API_KEY": "test-key",
    }
    with mock.patch.dict(os.environ, environment, clear=False):
        with mock.patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=truncated,
        ) as post:
            client = DeepSeekClient(model="deepseek-chat")
            assert client.chat([{"role": "user", "content": "Return text."}]) == (
                "legacy partial content"
            )
            with pytest.raises(EditorialServiceExhausted) as exc_info:
                client.chat(
                    [{"role": "user", "content": "Return text."}],
                    max_tokens=6_000,
                    strict_output_budget=True,
                )

    assert exc_info.value.failure_kind == "output_budget"
    assert post.call_count == 2


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


def test_deepseek_source_fallback_budget_rejects_nonempty_truncation_without_growth() -> None:
    truncated = mock.Mock()
    truncated.status_code = 200
    truncated.headers = {}
    truncated.raise_for_status.return_value = None
    truncated.json.return_value = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"title":"clipped"}'},
            }
        ],
        "usage": {"completion_tokens": 8_000},
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
            return_value=truncated,
        ) as post:
            client = DeepSeekClient(model="deepseek-chat")
            with pytest.raises(EditorialServiceExhausted) as exc_info:
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    strict_output_budget=True,
                )

    assert exc_info.value.failure_kind == "output_budget"
    assert post.call_count == 1
    assert post.call_args.kwargs["json"]["max_tokens"] == 8_000


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


def test_named_regions_receive_priority_anchor_queries() -> None:
    topic = "Optical Modules and Fibre: China Supply Depth and Gulf Connectivity"
    queries = _regional_anchor_queries(topic)
    assert _required_source_regions(topic) == ["china", "gulf"]
    assert any("stock exchange" in query.lower() for query in queries)
    assert any(
        all(token in query.lower() for token in ("gulf", "gcc", "regulator", "operator"))
        for query in queries
    )
    assert any("site:tdra.gov.ae" in query for query in queries)


def test_curated_primary_source_order_is_stable() -> None:
    results = _direct_source_candidates(
        "China semiconductor lithography equipment official filings"
    )
    assert [result.title for result in results[:4]] == [
        "NAURA Technology 2025 Annual Report",
        "AMEC 2025 Interim Report",
        "AMEC Shanghai Stock Exchange Company Profile",
        "ASML 2025 Annual Report",
    ]


def test_region_names_are_not_treated_as_company_identity_tokens() -> None:
    assert _claim_identity_tokens(
        "Gulf connectivity capacity reached 100 Gbps in 2025."
    ) == set()
    assert _claim_identity_tokens(
        "China semiconductor equipment revenue reached USD 2 billion in 2025."
    ) == set()


def test_region_matching_uses_source_evidence_not_search_query_only() -> None:
    unrelated = {
        "title": "U.S. grid resilience",
        "url": "https://energy.gov/grid",
        "domain": "energy.gov",
        "query": "China Gulf optical connectivity",
        "content": "United States electricity infrastructure evidence.",
    }
    operator = {
        "title": "Regional fibre expansion",
        "url": "https://www.eand.com/en/news/fibre-expansion.html",
        "domain": "eand.com",
        "content": "The operator expanded connectivity across the United Arab Emirates.",
    }
    assert not _source_matches_region(unrelated, "china")
    assert not _source_matches_region(unrelated, "gulf")
    assert _source_matches_region(operator, "gulf")


def test_exchange_and_gulf_policy_domains_are_regional_authority_anchors() -> None:
    china_sources = [
        {
            "title": "NAURA Technology 2025 Annual Report",
            "url": "https://static.cninfo.com.cn/finalpage/2026-04-18/1225122918.PDF",
        },
        {
            "title": "AMEC 2025 Interim Report",
            "url": "https://star.sse.com.cn/disclosure/listedinfo/announcement/report.pdf",
        },
    ]
    gulf_sources = [
        {
            "title": "Saudi National Semiconductor Hub",
            "url": "https://rdia.gov.sa/en/programs/infrastructure/national-semiconductor-hub-1/",
        },
        {
            "title": "UAE Operation 300Bn Industrial Strategy",
            "url": "https://www.moiat.gov.ae/en/about-us/about-the-strategy",
        },
        {
            "title": "Saudi National Industrial Strategy",
            "url": "https://www.vision2030.gov.sa/media/national-industrial-strategy.pdf",
        },
    ]

    assert all(_source_is_regional_anchor(source, "china") for source in china_sources)
    assert all(_source_is_regional_anchor(source, "gulf") for source in gulf_sources)


def test_optical_topic_rejects_generic_grid_storage_anchors() -> None:
    source = SourceDocument(
        title="U.S. DOE Office of Electricity",
        url="https://www.energy.gov/oe/office-electricity",
        query="optical connectivity power capacity",
        snippet="Public source on grid modernization and storage programs.",
        content="The Office of Electricity supports grid modernization and grid-scale storage. " * 10,
        domain="energy.gov",
    )
    assert _source_is_topic_contamination(
        source,
        "Optical Modules and Fibre: China Supply Depth and Gulf Connectivity",
    )
    assert not _source_is_topic_contamination(source, "Grid-scale energy storage in the Gulf")


def test_named_region_evidence_reserves_gulf_operator_points() -> None:
    china_sources = [
        SourceDocument(
            title=f"China optical filing {index}",
            url=f"https://www1.hkexnews.hk/china-optical-{index}.pdf",
            query="China optical filing",
            snippet=f"China optical manufacturer reported {40 + index}% growth in 2025.",
            content=(f"China optical manufacturer reported {40 + index}% growth in 2025. " * 20),
            source_type="pdf",
            domain="www1.hkexnews.hk",
        )
        for index in range(8)
    ]
    gulf_sources = [
        SourceDocument(
            title="Ooredoo launches 100 Gbps connectivity",
            url="https://www.ooredoo.qa/connectivity",
            query="Gulf optical connectivity",
            snippet="Qatar service tiers include 1 Gbps, 10 Gbps, 40 Gbps and 100 Gbps.",
            content=(
                "Qatar service tiers include 1 Gbps, 10 Gbps, 40 Gbps and 100 Gbps, with 99.9% availability in 2025. "
                * 12
            ),
            domain="ooredoo.qa",
        ),
        SourceDocument(
            title="Saudi Internet Report 2025",
            url="https://www.cst.gov.sa/internet-report",
            query="Gulf connectivity regulator",
            snippet="Saudi fixed-network performance reached 216 Mbps in 2025.",
            content=("Saudi fixed-network performance reached 216 Mbps and 99.6% coverage in 2025. " * 12),
            domain="cst.gov.sa",
        ),
    ]
    sources = [*china_sources, *gulf_sources]
    plan = {"objective": "Optical connectivity", "decision_question": "What is documented?"}
    fact_pack = build_research_fact_pack("Optical connectivity", plan, sources)
    evidence = _merge_named_region_evidence(
        topic="Optical Modules and Fibre: China Supply Depth and Gulf Connectivity",
        brief="Evidence-led",
        sources=sources,
        fact_pack=fact_pack,
        plan=plan,
        limit=12,
        per_region=3,
    )
    gulf_urls = {source.url for source in gulf_sources}
    gulf_rows = [row for row in evidence if row["source_url"] in gulf_urls]
    assert len(gulf_rows) >= 3
    assert len({row["source_url"] for row in gulf_rows}) == 2


def test_named_region_evidence_keeps_two_sources_when_one_has_many_metrics() -> None:
    metric_heavy = SourceDocument(
        title="Saudi National Industrial Strategy",
        url="https://www.vision2030.gov.sa/media/strategy.pdf",
        query="Gulf semiconductor industrial strategy",
        snippet="Saudi advanced manufacturing strategy.",
        content=(
            "Saudi advanced manufacturing targets 45% localization, 40 projects, USD 12 billion of investment and 90% supplier coverage by 2030. "
            * 12
        ),
        source_type="pdf",
        domain="vision2030.gov.sa",
    )
    policy = SourceDocument(
        title="Saudi National Semiconductor Hub",
        url="https://rdia.gov.sa/en/programs/infrastructure/national-semiconductor-hub-1/",
        query="Gulf semiconductor policy",
        snippet="Official semiconductor design and manufacturing program.",
        content=(
            "The Saudi National Semiconductor Hub launched in 2026 to coordinate semiconductor design, manufacturing, talent and startup development. "
            * 12
        ),
        domain="rdia.gov.sa",
    )
    sources = [metric_heavy, policy]
    topic = "Semiconductor equipment and lithography in the Gulf"
    plan = {"objective": topic, "decision_question": "What is documented?"}
    fact_pack = build_research_fact_pack(topic, plan, sources)

    evidence = _merge_named_region_evidence(
        topic=topic,
        brief="Saudi semiconductor industrial relevance",
        sources=sources,
        fact_pack=fact_pack,
        plan=plan,
        limit=8,
        per_region=5,
    )

    gulf_rows = [row for row in evidence if _source_matches_region(row, "gulf")]
    assert len(gulf_rows) >= 3
    assert len({row["source_url"] for row in gulf_rows}) == 2


def test_named_region_evidence_keeps_two_authorities_when_second_has_only_a_date() -> None:
    metric_heavy = SourceDocument(
        title="Saudi National Industrial Strategy",
        url="https://www.vision2030.gov.sa/media/strategy.pdf",
        query="Gulf semiconductor industrial strategy",
        snippet="Saudi advanced manufacturing strategy.",
        content=(
            "Saudi industrial capacity reached 45 percent across 40 projects with USD 12 billion of investment in 2025. "
            * 12
        ),
        source_type="pdf",
        domain="vision2030.gov.sa",
    )
    dated_policy = SourceDocument(
        title="Saudi National Semiconductor Hub",
        url="https://rdia.gov.sa/en/programs/infrastructure/national-semiconductor-hub-1/",
        query="Gulf semiconductor policy",
        snippet="Official semiconductor design and manufacturing program.",
        content=(
            "The Saudi National Semiconductor Hub launched in 2026 to coordinate semiconductor design, "
            "manufacturing, talent and startup development. " * 12
        ),
        domain="rdia.gov.sa",
    )
    unrelated = SourceDocument(
        title="UAE construction material statistics",
        url="https://example.ae/construction",
        query="Gulf industry",
        snippet="UAE stone market statistics.",
        content="UAE stone imports represented 90 percent of demand in 2025. " * 12,
        domain="example.ae",
    )
    sources = [metric_heavy, dated_policy, unrelated]
    topic = "Semiconductor equipment and lithography in the Gulf"
    plan = {"objective": topic, "decision_question": "What is documented?"}
    fact_pack = build_research_fact_pack(topic, plan, sources)

    evidence = _merge_named_region_evidence(
        topic=topic,
        brief="Saudi semiconductor industrial relevance",
        sources=sources,
        fact_pack=fact_pack,
        plan=plan,
        limit=8,
        per_region=5,
    )

    authority_urls = {metric_heavy.url, dated_policy.url}
    authority_rows = [row for row in evidence if row["source_url"] in authority_urls]
    assert len(authority_rows) >= 3
    assert {row["source_url"] for row in authority_rows} == authority_urls


def test_decimal_percent_is_not_split_into_false_fragment() -> None:
    source = SourceDocument(
        title="Operator service-level release",
        url="https://www.ooredoo.qa/service-level",
        query="Gulf connectivity",
        snippet="Service level availability reaches 99.9%.",
        content="The service-level agreement provides up to 99.9% availability for enterprise connectivity.",
        domain="ooredoo.qa",
    )
    plan = {"objective": "Gulf connectivity", "decision_question": "What is documented?"}
    fact_pack = build_research_fact_pack("Gulf connectivity", plan, [source])
    evidence = build_evidence_ledger("Gulf connectivity", [source], fact_pack, plan=plan)
    assert any(row["display_value"] == "99.9%" for row in evidence)
    assert not any(row["display_value"] == "9%" for row in evidence)


def test_sparse_two_metric_exhibit_is_completed_with_comparison_panel() -> None:
    exhibit = _complete_sparse_exhibit(
        {
            "heading": "Designed capacity increased",
            "metrics": [
                {"value": "0.6M", "label": "Capacity in December 2025", "note": "Exchange filing"},
                {"value": "1.6M", "label": "Capacity in April 2026", "note": "Exchange filing"},
            ],
            "panels": [],
        }
    )
    assert exhibit["panels"][0]["type"] == "comparison"
    assert _exhibit_information_units(exhibit) >= 6


def test_editorial_paragraphs_remove_non_usd_units_and_trim_density() -> None:
    paragraph = (
        "The filing reported RMB 200 million of revenue. "
        + "Evidence supports operating scale and delivery capacity across the optical supply chain. " * 14
    )
    cleaned = _sanitize_editorial_paragraphs(
        {"paragraphs": [paragraph, paragraph, paragraph, paragraph]},
        maximum_words_per_paragraph=100,
    )
    assert not any("RMB" in item for item in cleaned["paragraphs"])
    assert all(len(item.split()) <= 100 for item in cleaned["paragraphs"])


def test_source_packet_requires_each_named_region() -> None:
    sources = [
        {
            "title": f"China official filing {index}",
            "url": f"https://disc.static.szse.cn/report-{index}.pdf",
            "domain": "disc.static.szse.cn",
            "source_type": "pdf",
            "content": "China optical module manufacturing evidence " * 40,
        }
        for index in range(8)
    ]
    with pytest.raises(Exception, match="gulf"):
        _source_packet(
            {"sources": sources},
            topic="Optical Modules and Fibre: China Supply Depth and Gulf Connectivity",
        )


def test_chapter_named_region_requires_matching_source_id() -> None:
    chapter = {
        "number": "04",
        "title": "Gulf connectivity demand",
        "deck": "Saudi and Qatari operators are expanding fibre interconnection.",
        "callout": "Operator evidence anchors regional demand.",
        "opening": "Regional interconnection is expanding.",
        "subsections": [
            {"heading": f"Layer {index}", "paragraphs": ["Evidence " * 45, "Context " * 45]}
            for index in range(4)
        ],
        "sourceIds": ["S1", "S2"],
    }
    content = {
        "executiveSummary": {"paragraphs": ["Evidence " * 85] * 4, "sourceIds": ["S1", "S2"]},
        "chapters": [chapter] * 4,
        "exhibits": [],
        "outlook": {"paragraphs": ["Evidence " * 75] * 3, "sourceIds": ["S1", "S2"]},
        "visuals": [
            {"id": identifier}
            for identifier in ("executive-summary", "chapter-1", "chapter-2", "chapter-3", "chapter-4")
        ],
    }
    source_map = {
        "S1": {"title": "China filing", "domain": "szse.cn", "url": "https://szse.cn/a", "content": "China filing"},
        "S2": {"title": "Global standard", "domain": "itu.int", "url": "https://itu.int/a", "content": "Global standard"},
    }
    issues = _editorial_issues(content, {"S1", "S2"}, {"S1"}, source_map)
    assert any("chapter 1 names gulf" in issue.lower() for issue in issues)


def test_numeric_claim_requires_exact_marker_and_subject_in_cited_source() -> None:
    source = {
        "title": "Linktel Technologies Hong Kong Listing Application",
        "domain": "hkexnews.hk",
        "url": "https://hkexnews.hk/linktel.pdf",
        "content": "Linktel is a Chinese company. Linktel designed capacity for 800G-and-above transceivers reached 1.6 million units in April 2026.",
    }
    assert _source_supports_claim(source, "Linktel capacity reached 1.6 million units in April 2026.")
    assert not _source_supports_claim(source, "Linktel capacity reached 43.3 million units in April 2026.")


def test_generic_filing_title_uses_document_body_for_subject_identity() -> None:
    source = {
        "title": "printmgr file",
        "domain": "hkexnews.hk",
        "url": "https://hkexnews.hk/application-proof.pdf",
        "content": "Linktel Technologies designed capacity for 800G-and-above transceivers reached 1.6 million units in April 2026.",
    }
    assert _source_supports_claim(source, "Linktel's designed capacity reached 1.6 million units in April 2026.")


def test_translated_metric_matches_chinese_primary_filing_by_value_and_company() -> None:
    source = {
        "title": "Fenghua Advanced Technology 2025 Interim Report",
        "domain": "static.cninfo.com.cn",
        "url": "https://static.cninfo.com.cn/fenghua-2025-interim.pdf",
        "content": "Fenghua Advanced Technology 2025 interim report. 汽车电子销售同比增长39%，研发投入持续增加。",
    }
    claim = (
        "Fenghua Advanced Technology: first-half 2025 operating indicators "
        "39% Automotive electronics sales growth Year-on-year increase"
    )
    assert _source_supports_claim(source, claim)
    assert not _source_supports_claim(source, claim.replace("39%", "49%"))


def test_exchange_url_date_can_support_filing_date_attribution() -> None:
    source = {
        "title": "printmgr file",
        "domain": "hkexnews.hk",
        "url": "https://hkexnews.hk/app/sehk/2026/documents/sehk26062902260.pdf",
        "content": "Linktel designed capacity reached 1.6 million units as of April 2026.",
    }
    claim = "Linktel's designed capacity reached 1.6 million units by April 2026, according to its listing application dated 29 June 2026."
    assert _source_supports_claim(source, claim)


def test_compound_numeric_sentence_can_be_supported_by_two_cited_filings() -> None:
    sources = [
        {
            "title": "YOFC 2025 Annual Results",
            "domain": "hkexnews.hk",
            "url": "https://hkexnews.hk/yofc.pdf",
            "content": "YOFC revenue rose 16.8% in 2025 and gross margin reached 30.7%.",
        },
        {
            "title": "Linktel listing application",
            "domain": "hkexnews.hk",
            "url": "https://hkexnews.hk/linktel.pdf",
            "content": "Linktel designed capacity for 800G-and-above transceivers reached 1.6 million units in April 2026.",
        },
    ]
    section = {
        "paragraphs": [
            "YOFC's 2025 revenue rose 16.8% with gross margin at 30.7%, while Linktel's designed capacity reached 1.6 million units by April 2026."
        ]
    }
    assert not _numeric_claim_issues(section, sources, "executive summary")


def test_single_company_metric_cannot_be_generalised_to_plural_suppliers() -> None:
    source = {
        "title": "Linktel Technologies Hong Kong Listing Application",
        "domain": "hkexnews.hk",
        "url": "https://hkexnews.hk/linktel.pdf",
        "content": "Linktel is a Chinese company. Linktel designed capacity for 800G-and-above transceivers reached 1.6 million units in April 2026.",
    }
    section = {
        "paragraphs": ["Chinese optical suppliers reached 1.6 million units of capacity in April 2026."],
    }
    issues = _numeric_claim_issues(section, [source], "executive summary")
    assert any("single-source company metric" in issue for issue in issues)


def test_country_specific_exhibit_rejects_metric_from_another_country() -> None:
    exhibit = {
        "heading": "Saudi Arabia's regulatory baseline",
        "metrics": [
            {
                "value": "2030",
                "label": "Qatar National Vision target",
                "note": "Ooredoo Doha IX press release",
            }
        ],
    }
    sources = [
        {
            "title": "Ooredoo Doha IX",
            "domain": "ooredoo.qa",
            "url": "https://ooredoo.qa/doha-ix",
            "content": "Doha IX advances Qatar National Vision 2030.",
        }
    ]
    issues = _exhibit_subject_issues(exhibit, sources, 4)
    assert any("framed as" in issue and "qatar" in issue.lower() for issue in issues)


def test_exhibit_comparison_rejects_unsourced_panel_value() -> None:
    exhibit = {
        "heading": "NAURA Technology revenue growth, 2025",
        "sourceIds": ["S1"],
        "metrics": [],
        "panels": [
            {
                "type": "comparison",
                "title": "Reported growth",
                "columns": ["Revenue", "R&D"],
                "items": [
                    {"metric": "Year-on-year growth", "left": "30.85%", "right": "19.6%"},
                ],
            }
        ],
    }
    sources = [
        {
            "title": "NAURA Technology 2025 Annual Report",
            "domain": "static.cninfo.com.cn",
            "url": "https://static.cninfo.com.cn/naura-2025.pdf",
            "content": "NAURA Technology 2025 annual report. 营业收入同比增长30.85%。",
        }
    ]

    issues = _exhibit_subject_issues(exhibit, sources, 1)

    assert any("19.6%" in issue and "panel value" in issue for issue in issues)
    assert not any("30.85%" in issue for issue in issues)


def test_architecture_prompt_forbids_self_calculated_currency_conversion() -> None:
    prompt = _architecture_prompt(
        title="Semiconductor Equipment",
        topic="China capability and Gulf industrial relevance",
        brief="Use primary evidence.",
        sources=[],
        evidence=[],
    )

    assert "Never calculate or estimate a currency conversion" in prompt
    assert "Do not represent missing evidence as a numeric zero" in prompt


def test_pdf_extraction_prioritises_relevant_operating_pages_over_boilerplate() -> None:
    document = fitz.open()
    cover = document.new_page()
    cover.insert_text((72, 72), "Application proof is in draft form. The exchange takes no responsibility for the contents.")
    for index in range(8):
        page = document.new_page()
        page.insert_text((72, 72), f"Legal restriction and distribution notice {index}.")
    evidence = document.new_page()
    evidence.insert_text(
        (72, 72),
        "Optical transceiver designed capacity reached 1.6 million units in April 2026, with 78.3% capacity utilization.",
    )
    payload = document.tobytes()
    document.close()

    extracted = _extract_pdf_text(payload, max_chars=3_000, max_pages=4, query="optical transceiver production capacity")
    assert "1.6 million units" in extracted
    assert "[PDF page 10]" in extracted


def test_citation_rows_preserve_all_selected_sources() -> None:
    source_map = {
        f"S{index}": {
            "title": f"Long underlying source title {index}",
            "domain": f"authority{index}.gov",
            "url": f"https://authority{index}.gov/long-publication-path",
        }
        for index in range(1, 6)
    }
    rows = _citation_rows(source_map, source_map)
    assert len(rows) == 5
    assert rows[-1].endswith("https://authority5.gov/long-publication-path")


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


def test_built_payload_uses_institutional_byline_and_approved_signatory() -> None:
    payload = _build_payload(
        slug="red-chips",
        title="Red Chips",
        publication_date="2026-08-23",
        content={
            "subtitle": "Decision brief",
            "coverSummary": "Evidence-led market context.",
            "executiveSummary": {
                "headline": "Executive summary",
                "deck": "Decision context",
                "paragraphs": ["Evidence-led finding."],
                "sourceIds": ["S1"],
            },
            "chapters": [],
            "exhibits": [],
            "outlook": {
                "title": "Outlook",
                "deck": "Conditional outlook",
                "callout": "Monitor execution.",
                "paragraphs": ["Conditions remain bounded."],
                "sourceIds": ["S1"],
            },
        },
        sources=[
            {
                "id": "S1",
                "title": "Official report",
                "domain": "example.gov",
                "url": "https://example.gov/report",
            }
        ],
        visuals={"executive-summary": {"path": "cover.jpg", "alt": "Cover"}},
    )

    assert payload["authors"] == []
    assert payload["authorsApproved"] is False
    assert payload["publicationSignatory"] == {"name": "Frank Feng", "role": "Managing Partner"}
    assert payload["publicationSignatoryApproved"] is True


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


def test_generic_pdf_metadata_title_uses_filing_identity() -> None:
    source = {
        "title": "printmgr file",
        "domain": "www1.hkexnews.hk",
        "url": "https://www1.hkexnews.hk/app/example.pdf",
        "content": "Application Proof of Linktel Technologies Co., Ltd. (the Company) WARNING",
    }
    assert _english_source_title(source) == "Linktel Technologies Co., Ltd Hong Kong Listing Application"


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


def test_page_composition_gate_allows_intentional_source_register() -> None:
    text = (
        "GATEX | EXECUTIVE INTELLIGENCE\nSOURCE REGISTER\nSources and methodology\n"
        + "Official publication and usage context https://example.gov/report. " * 12
    )

    assert "contains orphaned source notes" not in " ".join(_page_composition_issues(text, 10))


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


def test_sparse_exhibit_panel_is_replaced_with_dense_metric_comparison() -> None:
    exhibit = {
        "heading": "Optical module market outlook",
        "metrics": [
            {"value": "$5.3B", "label": "Telecom modules, 2026", "note": "DSBJ projection"},
            {"value": "$100B", "label": "AI interconnects, 2030", "note": "DSBJ projection"},
        ],
        "panels": [
            {
                "type": "scenario",
                "items": [
                    {"label": "2026", "range": "Telecom", "body": "Projected market size."},
                    {"label": "2028", "range": "3.2T", "body": "Expected adoption ramp."},
                    {"label": "2030", "range": "AI", "body": "Projected interconnect sales."},
                ],
            }
        ],
    }

    completed = _complete_exhibit_information_units(exhibit)

    assert completed["panels"][0]["type"] == "comparison"
    assert _exhibit_information_units(completed) >= 6


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
