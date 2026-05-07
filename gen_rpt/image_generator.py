from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFilter

from .deepseek_client import DeepSeekClient
from .theme import load_theme

THEME = load_theme()
PALETTE = THEME.get("palette", {})
DEFAULT_MAX_SECTION_IMAGES = 2
DEFAULT_IMAGE_TIMEOUT = 18


def generate_ai_image_assets(
    client: DeepSeekClient,
    topic: str,
    report: Dict[str, Any],
    assets_dir: Path,
    backup_dir: Path,
    *,
    language: str = "en",
) -> Dict[str, str]:
    """Generate Pollinations images.

    Fallback abstract images are allowed for the cover, but section fallback images
    are suppressed by default because they look like generic blue fillers. The
    backup/image_prompts.json file records whether each image came from
    Pollinations or from fallback/skipped generation.
    """
    if os.getenv("DISABLE_AI_IMAGES", "").lower() in {"1", "true", "yes"}:
        return {}

    max_section_images = _int_env("MAX_AI_SECTION_IMAGES", DEFAULT_MAX_SECTION_IMAGES)
    timeout_seconds = _int_env("AI_IMAGE_TIMEOUT", DEFAULT_IMAGE_TIMEOUT)
    show_fallback_images = os.getenv("SHOW_FALLBACK_IMAGES", "false").lower() in {"1", "true", "yes"}

    assets_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    prompt_records: List[Dict[str, str]] = []
    result: Dict[str, str] = {}

    cover_keywords = (
        f"{topic}; premium strategy report cover; sophisticated editorial business report background; "
        "realistic abstract landscape, oceanic energy, glass waves, deep blue and white, no readable words, no logo"
    )
    cover_prompt = _polish_prompt(client, cover_keywords)
    cover_path = assets_dir / "cover-background.png"
    status, reason = _download_or_fallback(cover_prompt, cover_path, kind="cover", timeout_seconds=timeout_seconds, allow_fallback=True)
    result["cover-background"] = f"assets/{cover_path.name}"
    prompt_records.append({"id": "cover-background", "keywords": cover_keywords, "prompt": cover_prompt, "url": _url(cover_prompt), "status": status, "reason": reason})

    for idx, section in enumerate(report.get("sections", [])[:max_section_images], start=1):
        keywords = (
            f"{section.get('title', '')}; {section.get('lead', '')}; {topic}; "
            "premium editorial strategy report image, photorealistic business environment, human-scale context, "
            "cinematic lighting, blue and white palette, clean composition, no readable text, no logo"
        )
        prompt = _polish_prompt(client, keywords)
        target = assets_dir / f"image-{idx}.png"
        status, reason = _download_or_fallback(prompt, target, kind="section", timeout_seconds=timeout_seconds, allow_fallback=show_fallback_images)
        if status == "pollinations" or show_fallback_images:
            result[f"image-{idx}"] = f"assets/{target.name}"
        prompt_records.append({"id": f"image-{idx}", "keywords": keywords, "prompt": prompt, "url": _url(prompt), "status": status, "reason": reason})
        time.sleep(0.1)

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
- premium strategy consulting report visual, suitable for a BCG-style publication
- photorealistic or high-end editorial visual, not generic blue filler
- elegant blue/white palette, clean composition
- avoid readable text, logos, marks, watermarks, UI and charts inside the image
"""
    try:
        data = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.25)
        prompt = str(data.get("prompt", "")).strip()
        if prompt:
            return _sanitize(prompt)
    except Exception:
        pass
    return _sanitize(f"Premium editorial strategy consulting report visual, photorealistic, blue and white palette, no readable text, no logo. Topic: {keywords}")


def _download_or_fallback(prompt: str, output_path: Path, *, kind: str, timeout_seconds: int, allow_fallback: bool) -> Tuple[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(_url(prompt), timeout=timeout_seconds)
        response.raise_for_status()
        tmp = output_path.with_suffix(".raw")
        tmp.write_bytes(response.content)
        with Image.open(tmp) as image:
            image = image.convert("RGB")
            image.save(output_path, format="PNG")
        tmp.unlink(missing_ok=True)
        return "pollinations", ""
    except Exception as exc:
        if allow_fallback:
            _fallback_image(output_path, kind=kind)
            return "fallback", str(exc)[:300]
        return "skipped_fallback_disabled", str(exc)[:300]


def _url(prompt: str) -> str:
    base = "https://image.pollinations.ai/prompt/"
    query = "?width=1024&height=1024&enhance=true&private=true&nologo=true&safe=true&model=flux"
    return base + quote(prompt, safe="") + query


def _sanitize(prompt: str) -> str:
    prompt = " ".join(str(prompt).replace("\n", " ").split())
    if "no readable text" not in prompt.lower():
        prompt += ", no readable text, no logos, no watermarks"
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
            r = int(navy[0] * (1 - t) + mid[0] * t + accent[0] * glow * 0.22)
            g = int(navy[1] * (1 - t) + mid[1] * t + accent[1] * glow * 0.22)
            b = int(navy[2] * (1 - t) + mid[2] * t + accent[2] * glow * 0.22)
            px[x, y] = (min(255, r), min(255, g), min(255, b))
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(10):
        y = 120 + i * 70
        draw.arc((-220, y - 120, 1240, y + 260), 190, 350, fill=(255, 255, 255, 18), width=2)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(output_path, format="PNG")


def _hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default
