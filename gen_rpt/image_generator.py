from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFilter

from .deepseek_client import DeepSeekClient
from .theme import load_theme

THEME = load_theme()
PALETTE = THEME.get("palette", {})
BRAND_NAME = THEME.get("brand_name", "BlueOcean")


def generate_ai_image_assets(
    client: DeepSeekClient,
    topic: str,
    report: Dict[str, Any],
    assets_dir: Path,
    backup_dir: Path,
    *,
    language: str = "zh",
) -> Dict[str, str]:
    """Generate cover and section images. Falls back to local abstract art.

    This function deliberately does not overwrite section.visual_hint. The LLM may
    point sections to chart ids; those chart links should remain intact so data
    exhibits are not replaced by decorative AI images.
    """
    if os.getenv("DISABLE_AI_IMAGES", "").lower() in {"1", "true", "yes"}:
        return {}

    assets_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    prompt_records: List[Dict[str, str]] = []
    result: Dict[str, str] = {}

    cover_keywords = (
        f"{topic}; premium strategy report cover; abstract ocean depth; blue corporate visual system; "
        "subtle flowing lines; no readable words; no brand mark; premium business research background"
    )
    cover_prompt = _polish_prompt(client, cover_keywords)
    cover_path = assets_dir / "cover-background.png"
    _download_or_fallback(cover_prompt, cover_path, kind="cover")
    result["cover-background"] = f"assets/{cover_path.name}"
    prompt_records.append({"id": "cover-background", "keywords": cover_keywords, "prompt": cover_prompt, "url": _url(cover_prompt)})

    for idx, section in enumerate(report.get("sections", [])[:8], start=1):
        keywords = (
            f"{section.get('title', '')}; {section.get('lead', '')}; {topic}; "
            "executive strategy presentation image; abstract analytical business visual; blue ocean palette; "
            "clean geometric lines; subtle data grid; no readable words; no brand mark"
        )
        prompt = _polish_prompt(client, keywords)
        target = assets_dir / f"image-{idx}.png"
        _download_or_fallback(prompt, target, kind="section")
        result[f"image-{idx}"] = f"assets/{target.name}"
        prompt_records.append({"id": f"image-{idx}", "keywords": keywords, "prompt": prompt, "url": _url(prompt)})
        time.sleep(0.2)

    (backup_dir / "image_prompts.json").write_text(json.dumps(prompt_records, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _polish_prompt(client: DeepSeekClient, keywords: str) -> str:
    system = "You are an image prompt engineer. Return JSON only."
    user = f"""
Rewrite the following keywords into one rich English image prompt.

Keywords: {keywords}

Return JSON only:
{{"prompt": "..."}}

Rules:
- English only
- premium management consulting visual
- BlueOcean corporate style
- dark blue and bright blue palette
- abstract analytical composition with clean lines and subtle data-grid feeling
- avoid readable text, logos, and marks inside the image
"""
    try:
        data = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.35)
        prompt = str(data.get("prompt", "")).strip()
        if prompt:
            return _sanitize(prompt)
    except Exception:
        pass
    return _sanitize(f"Premium management consulting visual, BlueOcean corporate style, dark blue and bright blue, abstract analytical composition, clean geometric lines, subtle data-grid feeling. Topic: {keywords}")


def _download_or_fallback(prompt: str, output_path: Path, *, kind: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(_url(prompt), timeout=90)
        response.raise_for_status()
        tmp = output_path.with_suffix(".raw")
        tmp.write_bytes(response.content)
        with Image.open(tmp) as image:
            image = image.convert("RGB")
            image.save(output_path, format="PNG")
        tmp.unlink(missing_ok=True)
        return
    except Exception:
        _fallback_image(output_path, kind=kind)


def _url(prompt: str) -> str:
    base = "https://image.pollinations.ai/prompt/"
    query = "?width=1024&height=1024&enhance=true&private=true&nologo=true&safe=true&model=flux"
    return base + quote(prompt, safe="") + query


def _sanitize(prompt: str) -> str:
    prompt = " ".join(str(prompt).replace("\n", " ").split())
    if "avoid readable text" not in prompt.lower():
        prompt += ", avoid readable text, logos, and marks inside the image"
    return prompt[:900]


def _fallback_image(output_path: Path, *, kind: str) -> None:
    width, height = 1024, 1024
    navy = _hex(PALETTE.get("navy_dark", "#051C2C"))
    accent = _hex(PALETTE.get("bright_blue", "#3273F6"))
    mid = _hex(PALETTE.get("medium_blue", "#0055A4"))
    img = Image.new("RGB", (width, height), navy)
    px = img.load()
    for y in range(height):
        for x in range(width):
            t = (x * 0.55 + y * 0.45) / (width + height)
            glow = max(0.0, 1.0 - (((x - width * 0.78) / 360) ** 2 + ((y - height * 0.25) / 300) ** 2))
            r = int(navy[0] * (1 - t) + mid[0] * t + accent[0] * glow * 0.28)
            g = int(navy[1] * (1 - t) + mid[1] * t + accent[1] * glow * 0.28)
            b = int(navy[2] * (1 - t) + mid[2] * t + accent[2] * glow * 0.28)
            px[x, y] = (min(255, r), min(255, g), min(255, b))
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(15):
        y = 120 + i * 55
        draw.arc((-220, y - 120, 1240, y + 260), 190, 350, fill=(255, 255, 255, 22), width=2)
    draw.ellipse((610, 120, 1180, 690), fill=(60, 150, 190, 36))
    draw.ellipse((-160, 690, 520, 1270), fill=(70, 170, 215, 30))
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(output_path, format="PNG")


def _hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
