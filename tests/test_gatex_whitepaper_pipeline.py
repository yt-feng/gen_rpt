from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from gen_rpt.deepseek_client import DeepSeekClient, _completion_content
from gen_rpt.gatex_whitepaper_pipeline import (
    _authors,
    _english_source_title,
    _generate_visuals,
    _normalize_panel,
    _panel_renderability_issue,
    _paragraph_word_count,
    _payload_renderability_issues,
    _source_packet,
    _uniform_dark_region_issue,
    visual_quality_issues,
)


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


def test_completion_parser_accepts_sse_fallback() -> None:
    response = mock.Mock()
    response.json.side_effect = ValueError("not json")
    response.text = 'data: {"choices":[{"delta":{"content":"Gate"}}]}\n\ndata: {"choices":[{"delta":{"content":"X"}}]}\n\ndata: [DONE]\n'
    assert _completion_content(response) == "GateX"


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
    assert "https://example0.gov/report.pdf" in packet


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


def test_chinese_source_titles_are_rendered_as_english_citations() -> None:
    prospectus = {
        "title": "[PDF] 首次公开发行股票并在科创板上市招股说明书（注册稿）",
        "domain": "static.sse.com.cn",
    }
    company = {"title": "长鑫科技集团股份有限公司", "domain": "static.sse.com.cn"}
    assert _english_source_title(prospectus) == "STAR Market Initial Public Offering Prospectus (Registration Draft)"
    assert _english_source_title(company) == "ChangXin Memory Technologies Group Co., Ltd. Filing"


def test_malformed_comparison_panel_falls_back_to_populated_matrix() -> None:
    panel = {
        "type": "comparison",
        "columns": ["A", "B"],
        "items": [
            {"tag": "Foundry", "title": "Audited operating record", "body": "Listed-company evidence."},
            {"tag": "Memory", "title": "Prospectus record", "body": "Capacity and research evidence."},
            {"tag": "Cloud", "title": "Risk architecture", "body": "Infrastructure disclosure."},
        ],
    }
    normalized = _normalize_panel(panel)
    assert normalized["type"] == "matrix"
    assert len(normalized["items"]) == 3
    assert _panel_renderability_issue(normalized) == ""


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
