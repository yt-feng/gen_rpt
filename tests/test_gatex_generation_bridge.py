import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict
from unittest.mock import Mock, call, patch

import requests

from gen_rpt.deepseek_client import (
    DeepSeekClient,
    EditorialFormatContractError,
    EditorialServiceExhausted,
)
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_report_pipeline import (
    EditorialFailoverClient,
    ReportQualityError,
    _private_source_reproduction_violations,
    _source_artifact_dict,
)
from tools.gatex_generation_bridge import (
    BridgeError,
    _private_reference_url,
    _request_with_retries,
    _run_generator,
    build_intelligence_source_profile,
    discovered_sources_from_manifest,
    download_intelligence_seed_sources,
    download_private_sources,
    manifest_requires_private_source_content,
)
from tools.local_web_report_audit import required_reference_count, section_quality_issues


class GateXGenerationBridgeTests(unittest.TestCase):
    @staticmethod
    def _completion_response(content: str) -> Mock:
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": content},
                }
            ]
        }
        return response

    @staticmethod
    def _responses_response(payload: Dict[str, Any]) -> Mock:
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        response.text = json.dumps(payload)
        return response

    def test_editorial_failover_is_retry_exhaustion_only_sticky_and_secret_safe(self):
        class StubClient:
            def __init__(self, model: str, route: str, responses: list[object]) -> None:
                self.model = model
                self.route_label = route
                self.responses = list(responses)
                self.calls = 0

            def chat_json(self, *_args: object, **_kwargs: object) -> Dict[str, Any]:
                self.calls += 1
                response = self.responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response  # type: ignore[return-value]

        exhausted = EditorialServiceExhausted(
            "Chat Completions",
            failure_kind="retryable_http",
            status_code=500,
        )
        exhausted.args = ("SENTINEL_PRIVATE_UPSTREAM_BODY",)
        primary = StubClient("gpt-5.6-sol", "APIMart Chat", [exhausted])
        fallback = StubClient(
            "deepseek-chat",
            "DeepSeek Chat",
            [{"stage": "synthesis"}, {"stage": "revision"}],
        )
        client = EditorialFailoverClient(primary, fallback)  # type: ignore[arg-type]

        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(client.chat_json([]), {"stage": "synthesis"})
            self.assertEqual(client.chat_json([]), {"stage": "revision"})

        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 2)
        self.assertNotIn("SENTINEL_PRIVATE_UPSTREAM_BODY", stream.getvalue())
        self.assertEqual(
            client.route_record(),
            {
                "primaryModel": "gpt-5.6-sol",
                "primaryRoute": "APIMart Chat",
                "fallbackModel": "deepseek-chat",
                "fallbackRoute": "DeepSeek Chat",
                "activeModel": "deepseek-chat",
                "activeRoute": "DeepSeek Chat",
                "fallbackUsed": True,
                "failoverReason": "retryable_http",
            },
        )

    def test_editorial_failover_does_not_switch_on_schema_or_auth_failure(self):
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Chat")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        client = EditorialFailoverClient(primary, fallback)

        for failure in (ValueError("invalid report schema"), RuntimeError("HTTP 401")):
            primary.chat_json.side_effect = failure
            with self.assertRaises(type(failure)):
                client.chat_json([])

        fallback.chat_json.assert_not_called()
        self.assertFalse(client.route_record()["fallbackUsed"])

    def test_editorial_failover_does_not_switch_on_nonretryable_http_errors(self):
        for status_code in (400, 401, 403, 422):
            with self.subTest(status_code=status_code):
                response = requests.Response()
                response.status_code = status_code
                failure = requests.HTTPError(
                    f"HTTP {status_code}",
                    response=response,
                )
                primary = Mock(model="gpt-5.6-sol", route_label="APIMart Chat")
                fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
                primary.chat_json.side_effect = failure
                client = EditorialFailoverClient(primary, fallback)

                with self.assertRaises(requests.HTTPError) as raised:
                    client.chat_json(
                        [],
                        max_tokens=8_000,
                        fallback_max_tokens=8_000,
                        strict_output_budget=True,
                    )

                self.assertIs(raised.exception, failure)
                fallback.chat_json.assert_not_called()
                self.assertFalse(client.route_record()["fallbackUsed"])

    def test_source_strict_json_contract_failure_switches_once_and_sticks(self):
        primary = DeepSeekClient(
            api_key="primary-key",
            base_url="https://primary.example/v1",
            model="gpt-5.6-sol",
            provider="apimart",
        )
        fallback = DeepSeekClient(
            api_key="fallback-key",
            base_url="https://fallback.example/v1",
            model="deepseek-chat",
            provider="deepseek",
        )
        responses = [
            self._completion_response('{"title":"SENTINEL_UNTERMINATED"'),
            self._completion_response('{"title":"complete source report"}'),
            self._completion_response('{"title":"complete source revision"}'),
        ]
        client = EditorialFailoverClient(primary, fallback)
        kwargs = {
            "max_tokens": 8_000,
            "fallback_max_tokens": 8_000,
            "strict_output_budget": True,
        }
        environment = {
            "APIMART_USE_RESPONSES": "false",
            "APIMART_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_JSON_MODE": "true",
            "BACKEND_URL": "",
        }

        stream = io.StringIO()
        with patch.dict("os.environ", environment, clear=False), patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=responses,
        ) as post, redirect_stdout(stream):
            self.assertEqual(
                client.chat_json([{"role": "user", "content": "Return JSON."}], **kwargs),
                {"title": "complete source report"},
            )
            self.assertEqual(
                client.chat_json([{"role": "user", "content": "Revise JSON."}], **kwargs),
                {"title": "complete source revision"},
            )

        self.assertEqual(post.call_count, 3)
        self.assertEqual(
            [entry.args[0] for entry in post.call_args_list],
            [
                "https://primary.example/v1/chat/completions",
                "https://fallback.example/v1/chat/completions",
                "https://fallback.example/v1/chat/completions",
            ],
        )
        self.assertTrue(
            all(entry.kwargs["json"]["max_tokens"] == 8_000 for entry in post.call_args_list)
        )
        self.assertNotIn("SENTINEL_UNTERMINATED", stream.getvalue())
        self.assertEqual(client.route_record()["failoverReason"], "invalid_strict_json")
        self.assertTrue(client.route_record()["fallbackUsed"])

    def test_source_responses_empty_output_switches_once_to_strict_fallback(self):
        primary = DeepSeekClient(
            api_key="primary-key",
            base_url="https://primary.example/v1",
            model="gpt-5.6-sol",
            provider="apimart",
        )
        fallback = DeepSeekClient(
            api_key="fallback-key",
            base_url="https://fallback.example/v1",
            model="deepseek-chat",
            provider="deepseek",
        )
        client = EditorialFailoverClient(primary, fallback)
        environment = {
            "APIMART_USE_RESPONSES": "true",
            "APIMART_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_JSON_MODE": "true",
            "BACKEND_URL": "",
        }
        responses = [
            self._responses_response({"output_text": ""}),
            self._completion_response('{"title":"complete fallback report"}'),
        ]

        with patch.dict("os.environ", environment, clear=False), patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=responses,
        ) as post, patch.object(
            fallback,
            "chat_json",
            wraps=fallback.chat_json,
        ) as fallback_chat:
            self.assertEqual(
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    fallback_max_tokens=8_000,
                    strict_output_budget=True,
                ),
                {"title": "complete fallback report"},
            )

        self.assertEqual(post.call_count, 2)
        self.assertEqual(fallback_chat.call_count, 1)
        fallback_chat.assert_called_once_with(
            [{"role": "user", "content": "Return JSON."}],
            max_tokens=8_000,
            strict_output_budget=True,
        )
        self.assertEqual(
            [entry.args[0] for entry in post.call_args_list],
            [
                "https://primary.example/v1/responses",
                "https://fallback.example/v1/chat/completions",
            ],
        )
        self.assertTrue(client.route_record()["fallbackUsed"])
        self.assertEqual(
            client.route_record()["failoverReason"],
            "empty_structured_output",
        )

    def test_source_responses_empty_then_malformed_fallback_fails_closed(self):
        primary = DeepSeekClient(
            api_key="primary-key",
            base_url="https://primary.example/v1",
            model="gpt-5.6-sol",
            provider="apimart",
        )
        fallback = DeepSeekClient(
            api_key="fallback-key",
            base_url="https://fallback.example/v1",
            model="deepseek-chat",
            provider="deepseek",
        )
        client = EditorialFailoverClient(primary, fallback)
        environment = {
            "APIMART_USE_RESPONSES": "true",
            "APIMART_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_JSON_MODE": "true",
            "BACKEND_URL": "",
        }
        responses = [
            self._responses_response({"output_text": ""}),
            self._completion_response('{"title":"fallback remains unterminated"'),
        ]

        with patch.dict("os.environ", environment, clear=False), patch(
            "gen_rpt.deepseek_client.requests.post",
            side_effect=responses,
        ) as post, patch.object(
            fallback,
            "chat_json",
            wraps=fallback.chat_json,
        ) as fallback_chat:
            with self.assertRaises(EditorialFormatContractError) as raised:
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    fallback_max_tokens=8_000,
                    strict_output_budget=True,
                )

        self.assertEqual(raised.exception.failure_kind, "invalid_strict_json")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(fallback_chat.call_count, 1)
        self.assertTrue(client.route_record()["fallbackUsed"])
        self.assertEqual(
            client.route_record()["failoverReason"],
            "empty_structured_output",
        )

    def test_source_valid_json_wrong_top_level_schema_does_not_switch(self):
        primary = DeepSeekClient(
            api_key="primary-key",
            base_url="https://primary.example/v1",
            model="gpt-5.6-sol",
            provider="apimart",
        )
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        client = EditorialFailoverClient(primary, fallback)
        environment = {
            "APIMART_USE_RESPONSES": "false",
            "APIMART_RETRY_ATTEMPTS": "1",
            "DEEPSEEK_JSON_MODE": "true",
            "BACKEND_URL": "",
        }

        with patch.dict("os.environ", environment, clear=False), patch(
            "gen_rpt.deepseek_client.requests.post",
            return_value=self._completion_response("[]"),
        ) as post:
            with self.assertRaisesRegex(ValueError, "Expected a JSON object"):
                client.chat_json(
                    [{"role": "user", "content": "Return JSON."}],
                    max_tokens=8_000,
                    fallback_max_tokens=8_000,
                    strict_output_budget=True,
                )

        post.assert_called_once()
        fallback.chat_json.assert_not_called()
        self.assertFalse(client.route_record()["fallbackUsed"])

    def test_source_fallback_invalid_strict_json_fails_closed_without_repair(self):
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Chat")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary_failure = EditorialFormatContractError(
            "APIMart Chat",
            failure_kind="invalid_strict_json",
        )
        fallback_failure = EditorialFormatContractError(
            "DeepSeek Chat",
            failure_kind="invalid_strict_json",
        )
        primary.chat_json.side_effect = primary_failure
        fallback.chat_json.side_effect = fallback_failure
        client = EditorialFailoverClient(primary, fallback)

        with self.assertRaises(EditorialFormatContractError) as raised:
            client.chat_json(
                [],
                max_tokens=8_000,
                fallback_max_tokens=8_000,
                strict_output_budget=True,
            )

        self.assertIs(raised.exception, fallback_failure)
        primary.chat_json.assert_called_once_with(
            [],
            max_tokens=8_000,
            strict_output_budget=True,
        )
        fallback.chat_json.assert_called_once_with(
            [],
            max_tokens=8_000,
            strict_output_budget=True,
        )

    def test_format_contract_failover_is_strict_source_boundary_only(self):
        failure = EditorialFormatContractError(
            "APIMart Chat",
            failure_kind="invalid_strict_json",
        )
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Chat")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary.chat_json.side_effect = failure
        fallback.chat_json.return_value = {"title": "strict fallback"}
        client = EditorialFailoverClient(primary, fallback)

        with self.assertRaises(EditorialFormatContractError):
            client.chat_json([], max_tokens=8_000)

        fallback.chat_json.assert_not_called()
        self.assertFalse(client.route_record()["fallbackUsed"])
        self.assertEqual(
            client.chat_json(
                [],
                max_tokens=8_000,
                fallback_max_tokens=8_000,
                strict_output_budget=True,
            ),
            {"title": "strict fallback"},
        )
        fallback.chat_json.assert_called_once_with(
            [],
            max_tokens=8_000,
            strict_output_budget=True,
        )

    def test_editorial_failover_does_not_switch_on_report_quality_failure(self):
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Chat")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary.chat_json.side_effect = ReportQualityError(
            "source-channel publication contract failed closed"
        )
        client = EditorialFailoverClient(primary, fallback)

        with self.assertRaises(ReportQualityError):
            client.chat_json([])

        fallback.chat_json.assert_not_called()
        self.assertFalse(client.route_record()["fallbackUsed"])

    def test_source_output_budget_failover_maps_route_budgets_and_sticks(self):
        exhausted = EditorialServiceExhausted(
            "Responses API",
            failure_kind="output_budget",
            status_code=200,
        )
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Responses")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary.chat_json.side_effect = exhausted
        fallback.chat_json.side_effect = [
            {"title": "complete source report"},
            {"title": "complete source revision"},
        ]
        client = EditorialFailoverClient(primary, fallback)

        kwargs = {
            "max_tokens": 8_000,
            "fallback_max_tokens": 8_000,
            "strict_output_budget": True,
        }
        self.assertEqual(client.chat_json([], **kwargs)["title"], "complete source report")
        self.assertEqual(client.chat_json([], **kwargs)["title"], "complete source revision")

        primary.chat_json.assert_called_once_with(
            [],
            max_tokens=8_000,
            strict_output_budget=True,
        )
        self.assertEqual(fallback.chat_json.call_count, 2)
        for fallback_call in fallback.chat_json.call_args_list:
            self.assertEqual(fallback_call.kwargs["max_tokens"], 8_000)
            self.assertTrue(fallback_call.kwargs["strict_output_budget"])
        self.assertEqual(client.route_record()["failoverReason"], "output_budget")

    def test_source_output_budget_fails_closed_when_both_routes_exhaust(self):
        primary_failure = EditorialServiceExhausted(
            "Responses API",
            failure_kind="output_budget",
            status_code=200,
        )
        fallback_failure = EditorialServiceExhausted(
            "Chat Completions",
            failure_kind="output_budget",
            status_code=200,
        )
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Responses")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary.chat_json.side_effect = primary_failure
        fallback.chat_json.side_effect = fallback_failure
        client = EditorialFailoverClient(primary, fallback)

        with self.assertRaises(EditorialServiceExhausted) as raised:
            client.chat_json(
                [],
                max_tokens=8_000,
                fallback_max_tokens=8_000,
                strict_output_budget=True,
            )

        self.assertIs(raised.exception, fallback_failure)
        primary.chat_json.assert_called_once()
        fallback.chat_json.assert_called_once_with(
            [],
            max_tokens=8_000,
            strict_output_budget=True,
        )

    def test_typed_nonavailability_failure_does_not_stick_to_fallback(self):
        primary = Mock(model="gpt-5.6-sol", route_label="APIMart Responses")
        fallback = Mock(model="deepseek-chat", route_label="DeepSeek Chat")
        primary.chat_json.side_effect = EditorialServiceExhausted(
            "Responses API",
            failure_kind="invalid_schema",
            status_code=200,
        )
        client = EditorialFailoverClient(primary, fallback)

        with self.assertRaises(EditorialServiceExhausted):
            client.chat_json([])

        fallback.chat_json.assert_not_called()
        self.assertFalse(client.route_record()["fallbackUsed"])

    def test_bridge_builds_independent_deepseek_fallback_for_apimart(self):
        primary = Mock(
            model="gpt-5.6-sol",
            route_label="APIMart Chat",
            use_apimart=True,
            timeout=180,
        )
        fallback = Mock(
            model="deepseek-chat",
            route_label="DeepSeek Chat",
            use_apimart=False,
            timeout=180,
        )
        with patch(
            "tools.gatex_generation_bridge.DeepSeekClient",
            side_effect=[primary, fallback],
        ) as client_class, patch(
            "tools.gatex_generation_bridge.WebReportPipeline"
        ) as pipeline_class:
            pipeline_class.return_value.build_report.return_value = {"ok": True}
            result = _run_generator(
                topic="Tracked market theme",
                language="zh",
                model="gpt-5.6-sol",
                source_mode="web_only",
                private_sources=[],
                seed_sources=[],
                output_dir=Path("/tmp/report"),
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            client_class.call_args_list,
            [
                call(model="gpt-5.6-sol"),
                call(
                    model="deepseek-chat",
                    timeout=180,
                    provider="deepseek",
                ),
            ],
        )
        editorial_client = pipeline_class.call_args.kwargs["client"]
        self.assertIsInstance(editorial_client, EditorialFailoverClient)
        self.assertIs(editorial_client.primary, primary)
        self.assertIs(editorial_client.fallback, fallback)

    def test_source_channel_local_audit_uses_shared_section_depth_contract(self):
        paragraph = (
            "Verified public evidence supports the conclusion while the causal mechanism, "
            "counterpoint, operating constraint, and management implication remain explicit "
            "for the accountable decision owner and the next documented review gate."
        )
        section = {
            "title": "Evidence supports bounded action",
            "lead": paragraph,
            "paragraphs": [paragraph, paragraph, paragraph],
            "evidence": [
                "Source A supports the claim (https://example.com/a).",
                "Source B corroborates it (https://example.org/b).",
            ],
        }

        source_issues = section_quality_issues(
            section,
            3,
            source_channel_profile=True,
        )
        generic_issues = section_quality_issues(
            section,
            3,
            source_channel_profile=False,
        )

        self.assertEqual(source_issues, [])
        self.assertTrue(any("too few paragraphs" in issue for issue in generic_issues))
        self.assertTrue(any("lacks depth" in issue for issue in generic_issues))
        self.assertTrue(any("lacks dates or numeric" in issue for issue in generic_issues))

    def test_source_channel_retry_inherits_private_content_requirement(self):
        manifest = {
            "provenanceType": "manual_retry",
            "effectiveProvenanceType": "source_channel",
            "requiresPrivateSourceContent": True,
            "discoveredSources": [],
        }

        self.assertTrue(manifest_requires_private_source_content(manifest))
        self.assertFalse(
            manifest_requires_private_source_content(
                {"provenanceType": "manual_retry", "discoveredSources": []}
            )
        )

    def test_callback_requests_never_follow_redirects(self):
        with patch("tools.gatex_generation_bridge.requests.request") as request:
            response = request.return_value
            response.status_code = 302
            response.ok = False
            response.text = "Redirected"

            with self.assertRaises(BridgeError):
                _request_with_retries(
                    "GET",
                    "https://gatex.fund/api/generation/jobs/job/sources",
                )

        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        response.close.assert_called_once()

    def test_source_channel_private_body_is_verified_and_passed_as_seed(self):
        source_text = (
            "Asterion Robotics uses counter-positioning stress tests before "
            "major product decisions.\n\n"
            "A second private paragraph explains the operating discipline."
        )
        source_bytes = source_text.encode("utf-8")
        digest = hashlib.sha256(source_bytes).hexdigest()

        class FakeApi:
            def download(self, download_url: str, target: Path) -> None:
                self.download_url = download_url
                target.write_bytes(source_bytes)

        manifest = {
            "provenanceType": "source_channel",
            "discoveredSources": [
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": "social",
                    "url": "https://example.com/source/1",
                    "title": "Tracked source",
                    "publisher": "Example publisher",
                    "excerpt": "E" * 180 + "SENTINEL_AFTER_QUOTE_CAP",
                    "contentHash": digest,
                    "contentSha256": digest,
                    "contentByteSize": len(source_bytes),
                    "contentType": "text/plain; charset=utf-8",
                    "contentUrl": (
                        "/api/generation/jobs/job/sources/source/content"
                    ),
                    "metadata": {
                        "deletionStatus": "active",
                        "reusePolicy": "original_summary_only",
                        "maxQuoteCharacters": 180,
                    },
                }
            ],
        }
        api = FakeApi()
        with TemporaryDirectory() as directory:
            documents, summaries = download_intelligence_seed_sources(
                api,
                manifest,
                Path(directory),
            )

            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0].content, source_text)
            self.assertTrue(documents[0].metadata["gatex_private_content"])
            self.assertEqual(documents[0].metadata["content_hash"], digest)
            self.assertEqual(len(documents[0].snippet), 180)
            self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", documents[0].snippet)
            self.assertEqual(summaries[0]["contentSha256"], digest)
            self.assertTrue(summaries[0]["privateContent"])

            source_profile = build_intelligence_source_profile(
                manifest,
                documents,
            )
            self.assertEqual(source_profile["mode"], "source_channel")
            self.assertIn("Asterion Robotics", source_profile["anchors"])
            serialized_profile = json.dumps(source_profile, ensure_ascii=False)
            self.assertNotIn("second private paragraph", serialized_profile)
            self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", serialized_profile)
            serialized_source_artifact = json.dumps(
                _source_artifact_dict(documents[0]),
                ensure_ascii=False,
            )
            self.assertNotIn(
                "SENTINEL_AFTER_QUOTE_CAP",
                serialized_source_artifact,
            )

            with patch("tools.gatex_generation_bridge.DeepSeekClient"), patch(
                "tools.gatex_generation_bridge.WebReportPipeline"
            ) as pipeline_class:
                pipeline_class.return_value.build_report.return_value = {"ok": True}
                result = _run_generator(
                    topic="Tracked market theme",
                    language="zh",
                    model="test-model",
                    source_mode="web_only",
                    private_sources=[],
                    seed_sources=documents,
                    output_dir=Path(directory),
                    source_profile=source_profile,
                )

        self.assertEqual(result, {"ok": True})
        passed_seed = pipeline_class.return_value.build_report.call_args.kwargs[
            "seed_sources"
        ][0]
        self.assertEqual(passed_seed.content, source_text)
        self.assertEqual(
            pipeline_class.return_value.build_report.call_args.kwargs[
                "source_profile"
            ]["mode"],
            "source_channel",
        )

    def test_source_channel_profile_fails_closed_without_verified_body(self):
        manifest = {
            "provenanceType": "manual_retry",
            "effectiveProvenanceType": "source_channel",
            "requiresPrivateSourceContent": True,
        }
        excerpt_only = SourceDocument(
            title="Tracked source",
            url="https://example.com/source",
            query="seed",
            snippet="Editor-approved excerpt",
            content="Excerpt-only seed",
            source_type="gatex_seed_social",
            domain="example.com",
            metadata={"source_id": "source-1", "gatex_seed": True},
        )

        with self.assertRaisesRegex(BridgeError, "verified private source content"):
            build_intelligence_source_profile(manifest, [excerpt_only])

    def test_trend_manifest_stays_excerpt_seed_and_requires_web_corroboration(self):
        documents, summaries = discovered_sources_from_manifest(
            {
                "provenanceType": "trend_proposal",
                "discoveredSources": [
                    {
                        "id": "trend-source",
                        "kind": "api",
                        "url": "https://example.com/trend",
                        "title": "Rising topic",
                        "publisher": "Trend API",
                        "excerpt": (
                            "The topic appeared across three independent sources."
                        ),
                        "claimIds": "invalid-string-shape",
                        "metadata": {"deletionStatus": "active"},
                    }
                ],
            }
        )

        self.assertEqual(len(documents), 1)
        self.assertIn("corroborate", documents[0].content)
        self.assertFalse(
            documents[0].metadata.get("gatex_private_content", False)
        )
        self.assertEqual(documents[0].metadata["claim_ids"], [])
        self.assertEqual(summaries[0]["status"], "ready")

    def test_private_source_body_is_redacted_from_pipeline_artifacts(self):
        document = SourceDocument(
            title="Private source",
            url="https://example.com/source",
            query="seed",
            snippet="S" * 180 + "SENTINEL_AFTER_QUOTE_CAP",
            content="SECRET FULL BODY " * 100,
            source_type="gatex_private_social",
            content_type="text/plain; charset=utf-8",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "max_quote_characters": 180,
            },
        )

        payload = _source_artifact_dict(document)

        self.assertEqual(payload["content"], "S" * 180)
        self.assertEqual(payload["snippet"], "S" * 180)
        self.assertNotIn("SECRET FULL BODY", payload["content"])
        self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", json.dumps(payload))
        self.assertTrue(payload["metadata"]["private_content_redacted"])

        exact_passage = "A" * 181
        document.content = f"prefix {exact_passage} suffix"
        self.assertEqual(
            _private_source_reproduction_violations(
                {"sections": [{"body": exact_passage}]},
                [document],
            ),
            ["Private source"],
        )
        self.assertEqual(
            _private_source_reproduction_violations(
                {"sections": [{"body": "A" * 180}]},
                [document],
            ),
            [],
        )

    def test_private_source_leak_guard_counts_unique_fragments_across_artifacts(self):
        segments = [
            f"private-segment-{index:02d}-" + chr(65 + index) * 55
            for index in range(5)
        ]
        document = SourceDocument(
            title="Public source title",
            url="https://example.com/source",
            query="seed",
            snippet="Approved public summary.",
            content=" | ".join(segments),
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "source_id": "source-1",
                "max_quote_characters": 180,
            },
        )

        artifacts = {
            "report": {"lead": segments[0]},
            "research_plan": {"note": segments[2]},
            "evidence": [{"fact": segments[4]}],
        }
        self.assertEqual(
            _private_source_reproduction_violations(artifacts, [document]),
            ["source-1"],
        )

        repeated_approved_excerpt = {
            "report": document.snippet,
            "evidence": [document.snippet, document.snippet],
        }
        self.assertEqual(
            _private_source_reproduction_violations(
                repeated_approved_excerpt,
                [document],
            ),
            [],
        )

    def test_downloaded_private_source_gets_retained_reference_identity(self):
        class FakeApi:
            def download(self, _download_url: str, target: Path) -> None:
                target.write_text("A board-grade source document " * 12, encoding="utf-8")

        manifest = {
            "sources": [
                {
                    "id": "document-123",
                    "fileName": "Board Strategy.txt",
                    "mimeType": "text/plain",
                    "downloadUrl": "/private/source",
                }
            ]
        }
        with TemporaryDirectory() as directory:
            documents, summaries = download_private_sources(FakeApi(), manifest, Path(directory))

        self.assertEqual(len(documents), 1)
        self.assertEqual(summaries[0]["status"], "ready")
        self.assertTrue(documents[0].url.startswith("private://gatex.collection/"))

    def test_private_reference_url_is_stable_opaque_and_unique(self):
        first = _private_reference_url("document-123", "Board Strategy.docx", 1)
        repeated = _private_reference_url("document-123", "Renamed.docx", 9)
        second = _private_reference_url("document-456", "Board Strategy.docx", 1)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("private://gatex.collection/"))
        self.assertNotIn("document-123", first)
        self.assertNotIn("Board", first)

    def test_private_reference_url_falls_back_to_file_identity(self):
        first = _private_reference_url("", "One.docx", 1)
        repeated = _private_reference_url("", "One.docx", 1)
        second = _private_reference_url("", "Two.docx", 2)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)

    def test_reference_target_tracks_available_sources_up_to_four(self):
        self.assertEqual(required_reference_count([]), 4)
        self.assertEqual(required_reference_count([{"url": "private://one"}]), 1)
        self.assertEqual(required_reference_count([{}, {}]), 4)
        self.assertEqual(required_reference_count([{"url": "private://one"}, {"url": "private://two"}]), 2)
        self.assertEqual(
            required_reference_count([{"url": f"https://example.com/{index}"} for index in range(5)]),
            4,
        )
        self.assertEqual(
            required_reference_count([{"url": "https://example.com"}, {"url": "https://example.com"}]),
            1,
        )
        self.assertEqual(required_reference_count({"unexpected": "shape"}), 4)


if __name__ == "__main__":
    unittest.main()
