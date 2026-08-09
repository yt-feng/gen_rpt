from __future__ import annotations

import os
from unittest import mock

from PIL import Image

from gen_rpt.deepseek_client import DeepSeekClient, _completion_content
from gen_rpt.gatex_whitepaper_pipeline import _authors, _paragraph_word_count, _source_packet, visual_quality_issues


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
