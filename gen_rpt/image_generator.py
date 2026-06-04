from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urlencode

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageStat

from .deepseek_client import DeepSeekClient
from .theme import load_theme

THEME = load_theme()
PALETTE = THEME.get("palette", {})
DEFAULT_MAX_SECTION_IMAGES = 10
DEFAULT_IMAGE_TIMEOUT = 45
DEFAULT_IMAGE_RETRIES = 3


def generate_ai_image_assets(
    client: DeepSeekClient,
    topic: str,
    report: Dict[str, Any],
    assets_dir: Path,
    backup_dir: Path,
    *,
    language: str = "en",
) -> Dict[str, str]:
    """Generate editorial visuals for the report.

    Important: the AI cover is written to cover-ai.png instead of the brand
    fallback cover-background.png. Earlier versions reused the already-created
    brand cover as a cache hit, which prevented Pollinations from being called.
    """
    if os.getenv("DISABLE_AI_IMAGES", "").lower() in {"1", "true", "yes"}:
        return {}

    max_section_images = _int_env("MAX_AI_SECTION_IMAGES", DEFAULT_MAX_SECTION_IMAGES)
    timeout_seconds = _int_env("AI_IMAGE_TIMEOUT", DEFAULT_IMAGE_TIMEOUT)
    retries = _int_env("AI_IMAGE_RETRIES", DEFAULT_IMAGE_RETRIES)
    allow_section_fallback = os.getenv("SHOW_FALLBACK_IMAGES", "true").lower() not in {"0", "false", "no"}

    assets_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    prompt_records: List[Dict[str, str]] = []
    result: Dict[str, str] = {}

    cover_keywords = (
        f"{topic}; full-page premium strategy report cover background; topic-specific editorial visual; "
        "show a sophisticated real-world or cinematic conceptual scene directly related to the researched industry or technology; "
        "executive publication quality; deep blue, white and electric-blue accents; no readable words; no logo; "
        "leave calm negative space for a white title card; avoid generic ocean waves, abstract blue filler and unrelated decorative gradients"
    )
    cover_prompt = _polish_prompt(client, cover_keywords)
    cover_path = assets_dir / "cover-ai.png"
    if cover_path.exists():
        cover_path.unlink(missing_ok=True)
    status, reason = _download_or_fallback(cover_prompt, cover_path, kind="cover", timeout_seconds=timeout_seconds, retries=retries, allow_fallback=True)
    result["cover-background"] = f"assets/{cover_path.name}"
    prompt_records.append({"id": "cover-background", "keywords": cover_keywords, "prompt": cover_prompt, "url": _url(cover_prompt), "status": status, "reason": reason})

    sections = report.get("sections", []) or []
    for idx, section in enumerate(sections[:max_section_images], start=1):
        title = _section_title_for_prompt(section, idx)
        lead = _shorten(section.get("lead", ""), 180)
        keywords = (
            f"{title}; {lead}; {topic}; premium editorial strategy report image; "
            "topic-specific real-world business, industrial, technology, policy, infrastructure or executive setting; "
            "human-scale context; cinematic lighting; blue and white accents; clean composition; no readable text; no logo; "
            "avoid generic abstract filler"
        )
        prompt = _polish_prompt(client, keywords)
        target = assets_dir / f"image-{idx}.png"
        status, reason = _download_or_fallback(prompt, target, kind="section", timeout_seconds=timeout_seconds, retries=retries, allow_fallback=allow_section_fallback)
        if target.exists() and target.stat().st_size > 0:
            result[f"image-{idx}"] = f"assets/{target.name}"
        prompt_records.append({"id": f"image-{idx}", "keywords": keywords, "prompt": prompt, "url": _url(prompt), "status": status, "reason": reason})
        time.sleep(0.25)

    _ensure_section_image_diversity(assets_dir, prompt_records, max_section_images)

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
- topic-specific visual metaphor or real-world scene; reflect the industry/technology instead of generic decoration
- photorealistic or high-end editorial visual, not generic blue filler
- elegant blue/white accents, clean composition
- avoid generic ocean waves, glass waves, water surfaces, abstract blue gradients unless the topic is explicitly ocean-related
- avoid readable text, logos, marks, watermarks, UI and charts inside the image
"""
    try:
        data = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.25)
        prompt = str(data.get("prompt", "")).strip()
        if prompt:
            return _sanitize(prompt)
    except Exception:
        pass
    return _sanitize(f"Premium topic-specific editorial strategy consulting report visual, photorealistic, blue and white accents, no readable text, no logo, avoid ocean waves and abstract blue filler. Topic: {keywords}")


def _download_or_fallback(prompt: str, output_path: Path, *, kind: str, timeout_seconds: int, retries: int, allow_fallback: bool) -> Tuple[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        try:
            with Image.open(output_path) as cached:
                cached.verify()
            return "cached", ""
        except Exception:
            output_path.unlink(missing_ok=True)

    last_error = ""
    for attempt in range(max(1, retries)):
        try:
            response = requests.get(_url(prompt), timeout=timeout_seconds, headers={"User-Agent": "BlueOceanReportGenerator/1.0"})
            response.raise_for_status()
            tmp = output_path.with_suffix(".raw")
            tmp.write_bytes(response.content)
            with Image.open(tmp) as image:
                image = image.convert("RGB")
                image.save(output_path, format="PNG")
            tmp.unlink(missing_ok=True)
            return "pollinations", ""
        except Exception as exc:
            last_error = str(exc)[:300]
            time.sleep(min(2.5 * (attempt + 1), 8.0))

    wiki_status, wiki_reason = _download_wikimedia_fallback(prompt, output_path, timeout_seconds=min(18, timeout_seconds))
    if wiki_status:
        return "wikimedia", wiki_reason or last_error

    if allow_fallback:
        _fallback_image(output_path, kind=kind, prompt=prompt)
        return "fallback", last_error
    return "skipped_no_fallback", last_error


def _ensure_section_image_diversity(assets_dir: Path, prompt_records: List[Dict[str, str]], max_section_images: int) -> None:
    seen_hashes: List[str] = []
    for idx in range(1, max_section_images + 1):
        path = assets_dir / f"image-{idx}.png"
        if not path.exists() or path.stat().st_size <= 0:
            continue
        digest = _image_hash(path)
        if not digest:
            continue
        if any(_hamming(digest, prior) <= 3 for prior in seen_hashes):
            record = next((item for item in prompt_records if item.get("id") == f"image-{idx}"), {})
            prompt = str(record.get("prompt") or record.get("keywords") or f"section image {idx}")
            digest = _replace_with_distinct_fallback(path, prompt, idx, seen_hashes) or digest
            if record:
                record["status"] = f"{record.get('status', 'image')}_deduped_fallback"
                record["reason"] = "near-duplicate section visual replaced with deterministic local fallback"
        seen_hashes.append(digest)


def _replace_with_distinct_fallback(path: Path, prompt: str, idx: int, seen_hashes: List[str]) -> str:
    scene_tokens = [
        ("boardroom", "commercial customer adoption boardroom"),
        ("economics", "capital cost economics bars"),
        ("policy", "policy regulation public institution"),
        ("network", "supply chain partner network"),
        ("infrastructure", "infrastructure grid facility"),
        ("dashboard", "dashboard quarterly milestone decision"),
        ("evidence", "evidence source validation desk"),
        ("control", "industrial technology control room"),
        ("portfolio", "portfolio options investment committee"),
        ("market", "market entry customer field visit"),
    ]
    best = ""
    start = idx % len(scene_tokens)
    ordered = scene_tokens[start:] + scene_tokens[:start]
    for attempt, (scene_key, scene) in enumerate(ordered, start=1):
        variant_prompt = (
            f"fallback_scene={scene_key}; distinct visual variant {idx}-{attempt}; {scene}; {prompt}; "
            "different composition from prior pages; avoid repeating prior page composition"
        )
        _fallback_image(path, kind="section", prompt=variant_prompt)
        digest = _image_hash(path)
        if not digest:
            continue
        best = digest
        if all(_hamming(digest, prior) > 3 for prior in seen_hashes):
            return digest
    return best


def _image_hash(path: Path) -> str:
    try:
        image = Image.open(path).convert("L").resize((16, 16))
        pixels = list(image.getdata())
        avg = sum(pixels) / max(1, len(pixels))
        return "".join("1" if value > avg else "0" for value in pixels)
    except Exception:
        return ""


def _hamming(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right)) + abs(len(left) - len(right))


def _url(prompt: str) -> str:
    base = "https://image.pollinations.ai/prompt/"
    query = "?width=1280&height=900&enhance=true&private=true&nologo=true&safe=true&model=flux"
    return base + quote(prompt, safe="") + query


def _sanitize(prompt: str) -> str:
    prompt = " ".join(str(prompt).replace("\n", " ").split())
    lower = prompt.lower()
    if "no readable text" not in lower:
        prompt += ", no readable text, no logos, no watermarks"
    if "avoid ocean waves" not in lower:
        prompt += ", avoid generic ocean waves and abstract blue filler"
    return prompt[:1100]


def _download_wikimedia_fallback(prompt: str, output_path: Path, *, timeout_seconds: int) -> Tuple[str, str]:
    query = _wikimedia_query(prompt)
    if not query:
        return "", "no wikimedia query"
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrsearch": query,
        "gsrlimit": "20",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "1280",
        "format": "json",
        "origin": "*",
    }
    try:
        response = requests.get(api, params=params, timeout=timeout_seconds, headers={"User-Agent": "BlueOceanReportGenerator/1.0"})
        response.raise_for_status()
        pages = list((response.json().get("query", {}).get("pages", {}) or {}).values())
    except Exception as exc:
        try:
            raw = _curl_bytes(api + "?" + urlencode(params), timeout_seconds)
            pages = list((json.loads(raw.decode("utf-8")).get("query", {}).get("pages", {}) or {}).values())
        except Exception as curl_exc:
            return "", f"wikimedia search failed: {str(exc)[:120]}; curl: {str(curl_exc)[:120]}"
    candidates: List[tuple[int, str, str]] = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        mime = str(info.get("mime") or "")
        title = str(page.get("title") or "")
        width = int(info.get("thumbwidth") or info.get("width") or 0)
        height = int(info.get("thumbheight") or info.get("height") or 0)
        if not url or "svg" in mime.lower() or url.lower().endswith(".svg"):
            continue
        if width and height and (width < 500 or height < 350):
            continue
        if _bad_wikimedia_title(title):
            continue
        candidates.append((_wikimedia_title_score(title, query), url, title))
    if not candidates:
        return "", "no suitable wikimedia image"
    candidates.sort(reverse=True, key=lambda item: item[0])
    start = int(hashlib.sha1(prompt.encode("utf-8", errors="ignore")).hexdigest()[:6], 16) % len(candidates)
    ordered = candidates[start:] + candidates[:start]
    for _score, url, title in ordered:
        try:
            image_response = requests.get(url, timeout=timeout_seconds, headers={"User-Agent": "BlueOceanReportGenerator/1.0"})
            image_response.raise_for_status()
            content = image_response.content
        except Exception:
            try:
                content = _curl_bytes(url, timeout_seconds)
            except Exception:
                continue
        try:
            tmp = output_path.with_suffix(".wiki")
            tmp.write_bytes(content)
            with Image.open(tmp) as image:
                image = image.convert("RGB")
                quality_reason = _wikimedia_image_reject_reason(image, title)
                if quality_reason:
                    tmp.unlink(missing_ok=True)
                    continue
                image = _cover_crop(image, 1280, 900)
                image.save(output_path, format="PNG")
            tmp.unlink(missing_ok=True)
            return "wikimedia", query
        except Exception:
            continue
    return "", "wikimedia downloads failed"


def _bad_wikimedia_title(title: str) -> bool:
    lower = str(title or "").lower()
    bad_tokens = (
        "logo",
        "icon",
        "map",
        "diagram",
        "chart",
        "graph",
        "table",
        "seal",
        "hearing",
        "treaty",
        "report",
        "serial",
        "committee",
        "document",
        "cover",
        "pdf",
        "book",
        "page",
        "text",
        "transcript",
        "minutes",
    )
    return any(token in lower for token in bad_tokens)


def _wikimedia_title_score(title: str, query: str) -> int:
    lower = str(title or "").lower()
    query_lower = str(query or "").lower()
    positives = (
        "tokamak",
        "iter",
        "fusion reactor",
        "plasma",
        "stellarator",
        "facility",
        "control room",
        "reactor",
        "construction",
        "laboratory",
        "power plant",
    )
    score = sum(10 for token in positives if token in lower)
    for token in query_lower.split():
        if len(token) >= 4 and token in lower:
            score += 2
    if lower.endswith((".jpg", ".jpeg", ".png")):
        score += 1
    return score


def _wikimedia_image_reject_reason(image: Image.Image, title: str) -> str:
    width, height = image.size
    if width < 500 or height < 350:
        return "too small"
    stat = ImageStat.Stat(image)
    mean = sum(float(x) for x in stat.mean) / 3
    stddev = sum(float(x) for x in stat.stddev) / 3
    gray = image.convert("L").resize((160, 112))
    pixels = list(gray.getdata())
    near_white = sum(1 for pixel in pixels if pixel >= 236) / max(1, len(pixels))
    near_gray = sum(1 for pixel in pixels if 105 <= pixel <= 190) / max(1, len(pixels))
    if stddev < 10:
        return "flat image"
    if mean > 218 and stddev < 42 and near_white > 0.48:
        return "pale document-like image"
    if near_gray > 0.68 and stddev < 34:
        return "gray scan-like image"
    if _bad_wikimedia_title(title):
        return "bad title"
    return ""


def _curl_bytes(url: str, timeout_seconds: int) -> bytes:
    run = subprocess.run(
        ["curl", "-L", "--max-time", str(max(3, timeout_seconds)), "-s", url],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(6, timeout_seconds + 5),
    )
    if not run.stdout:
        raise RuntimeError((run.stderr or b"empty curl response").decode("utf-8", errors="ignore")[:180])
    return run.stdout


def _wikimedia_query(prompt: str) -> str:
    lower = prompt.lower()
    if any(token in lower for token in ["fusion", "tokamak", "plasma", "tritium", "reactor"]):
        if any(token in lower for token in ["construction", "cost", "capital", "lcoe", "timing"]):
            return "ITER tokamak construction"
        if any(token in lower for token in ["control", "customer", "commercial", "bankability", "readiness"]):
            return "tokamak control room fusion"
        if any(token in lower for token in ["policy", "regulation", "government", "licensing"]):
            return "nuclear fusion research facility"
        return "tokamak fusion reactor"
    if any(token in lower for token in ["battery", "storage", "grid", "power", "hydrogen"]):
        return "energy storage power grid"
    if any(token in lower for token in ["rail", "railway", "train", "logistics"]):
        return "railway logistics terminal"
    if any(token in lower for token in ["manufacturing", "factory", "industrial", "supply chain"]):
        return "industrial manufacturing facility"
    return "business meeting technology"


def _cover_crop(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        return Image.new("RGB", (target_w, target_h), "white")
    scale = max(target_w / width, target_h / height)
    resized = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _fallback_image(output_path: Path, *, kind: str, prompt: str) -> None:
    width, height = 1280, 900
    navy = _hex(PALETTE.get("navy_dark", "#051C2C"))
    accent = _hex(PALETTE.get("bright_blue", "#3273F6"))
    mid = _hex(PALETTE.get("medium_blue", "#0055A4"))
    paper = (246, 249, 252)
    topic_type = _prompt_type(prompt)
    scene_type = _fallback_scene_type(prompt)
    variant = int(hashlib.sha1(prompt.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)

    img = Image.new("RGB", (width, height), paper if kind == "section" else navy)
    px = img.load()
    for y in range(height):
        for x in range(width):
            t = (x * 0.45 + y * 0.55) / (width + height)
            glow = max(0.0, 1.0 - (((x - width * 0.70) / 360) ** 2 + ((y - height * 0.38) / 250) ** 2))
            if kind == "cover":
                r = int(navy[0] * (1 - t) + mid[0] * t + accent[0] * glow * 0.22)
                g = int(navy[1] * (1 - t) + mid[1] * t + accent[1] * glow * 0.22)
                b = int(navy[2] * (1 - t) + mid[2] * t + accent[2] * glow * 0.22)
            else:
                r = int(paper[0] * (1 - t * 0.20) + accent[0] * t * 0.16 + accent[0] * glow * 0.08)
                g = int(paper[1] * (1 - t * 0.20) + accent[1] * t * 0.16 + accent[1] * glow * 0.08)
                b = int(paper[2] * (1 - t * 0.20) + accent[2] * t * 0.16 + accent[2] * glow * 0.08)
            px[x, y] = (min(255, r), min(255, g), min(255, b))

    draw = ImageDraw.Draw(img, "RGBA")
    line_color = (255, 255, 255, 84) if kind == "cover" else (0, 85, 164, 70)
    node_color = (255, 255, 255, 110) if kind == "cover" else (0, 48, 135, 100)
    electric = (*accent, 145)

    if kind == "section":
        _draw_section_backdrop(draw, topic_type, width, height, line_color, node_color, electric, variant)
        _draw_scene_overlay(draw, scene_type, width, height, line_color, node_color, electric, variant)
        img = img.filter(ImageFilter.SMOOTH_MORE)
        img.save(output_path, format="PNG")
        return

    if topic_type == "fusion":
        cx = int(width * (0.60 + (variant % 9) * 0.012))
        cy = int(height * (0.42 + ((variant // 9) % 7) * 0.012))
        for r in [70, 130, 210, 300]:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=line_color, width=4)
        for i in range(14):
            angle = (i / 14) * 6.28318 + (variant % 17) * 0.01
            x1 = cx + int(90 * math.cos(angle))
            y1 = cy + int(90 * math.sin(angle))
            x2 = cx + int(330 * math.cos(angle))
            y2 = cy + int(330 * math.sin(angle))
            draw.line((x1, y1, x2, y2), fill=line_color, width=2)
        draw.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), fill=electric)
        draw.rectangle((80, 660, 1180, 710), outline=line_color, width=3)
        for i in range(8):
            draw.rectangle((130 + i * 125, 610, 185 + i * 125, 780), outline=line_color, width=2)
    elif topic_type == "energy":
        for i in range(9):
            cx = 180 + i * 115
            cy = 330 + ((i % 3) - 1) * 70
            draw.ellipse((cx - 38, cy - 38, cx + 38, cy + 38), outline=line_color, width=4)
            if i > 0:
                draw.line((cx - 115 + 38, 330 + (((i - 1) % 3) - 1) * 70, cx - 38, cy), fill=line_color, width=3)
    elif topic_type == "rail":
        for offset in [0, 86, 172, 258]:
            draw.line((80, 680 - offset, 1180, 230 - offset), fill=line_color, width=5)
            draw.line((90, 735 - offset, 1190, 285 - offset), fill=line_color, width=5)
            for k in range(10):
                x = 150 + k * 105
                draw.line((x, 710 - offset, x + 62, 628 - offset), fill=line_color, width=2)
    else:
        points = [(120, 620), (300, 420), (470, 540), (650, 300), (830, 450), (1030, 260), (1170, 380)]
        for a, b in zip(points, points[1:]):
            draw.line((*a, *b), fill=line_color, width=4)
        for x, y in points:
            draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=node_color)

    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(output_path, format="PNG")


def _prompt_type(prompt: str) -> str:
    lower = prompt.lower()
    if any(token in lower for token in ["fusion", "tokamak", "plasma", "tritium", "reactor"]):
        return "fusion"
    if any(token in lower for token in ["rail", "railway", "train", "logistics", "coal"]):
        return "rail"
    if any(token in lower for token in ["energy", "battery", "power", "grid", "hydrogen", "storage"]):
        return "energy"
    return "business"


def _fallback_scene_type(prompt: str) -> str:
    lower = prompt.lower()
    explicit = re.search(r"fallback_scene=([a-z]+)", lower)
    if explicit:
        key = explicit.group(1)
        if key in {"boardroom", "economics", "policy", "network", "infrastructure", "dashboard", "evidence"}:
            return key
        if key in {"control", "portfolio", "market"}:
            return {
                "control": "infrastructure",
                "portfolio": "dashboard",
                "market": "boardroom",
            }[key]
    if any(token in lower for token in ["commercial", "customer", "bankability", "revenue", "adoption"]):
        return "boardroom"
    if any(token in lower for token in ["cost", "return", "timing", "capital", "lcoe"]):
        return "economics"
    if any(token in lower for token in ["regulation", "policy", "acceptance", "government"]):
        return "policy"
    if any(token in lower for token in ["supply", "partner", "talent", "chain"]):
        return "network"
    if any(token in lower for token in ["incumbent", "options", "portfolio", "grid"]):
        return "infrastructure"
    if any(token in lower for token in ["agenda", "quarter", "milestone", "decision"]):
        return "dashboard"
    if any(token in lower for token in ["facts", "noise", "evidence", "signal"]):
        return "evidence"
    return "boardroom"


def _draw_section_backdrop(draw: ImageDraw.ImageDraw, topic: str, width: int, height: int, line_color: tuple[int, int, int, int], node_color: tuple[int, int, int, int], electric: tuple[int, int, int, int], variant: int) -> None:
    draw.rectangle((0, 0, width, int(height * 0.66)), fill=(244, 248, 252, 232))
    draw.rectangle((0, int(height * 0.66), width, height), fill=(225, 234, 243, 238))
    draw.polygon([(0, height), (width, height), (width, int(height * 0.72)), (0, int(height * 0.88))], fill=(211, 224, 238, 210))

    if topic == "fusion":
        cx = int(width * (0.70 + (variant % 5) * 0.012))
        cy = int(height * 0.39)
        draw.rounded_rectangle((120, 500, 1160, 650), radius=22, outline=line_color, width=4, fill=(255, 255, 255, 70))
        draw.ellipse((cx - 230, cy - 190, cx + 230, cy + 190), outline=(0, 85, 164, 96), width=12)
        draw.ellipse((cx - 135, cy - 105, cx + 135, cy + 105), outline=(50, 115, 246, 116), width=8)
        draw.ellipse((cx - 52, cy - 52, cx + 52, cy + 52), fill=electric)
        for x in range(170, 1110, 120):
            draw.line((x, 500, x + 72, 650), fill=(0, 85, 164, 54), width=3)
    elif topic == "energy":
        for x in range(120, 1180, 150):
            draw.line((x, 535, x + 70, 235), fill=line_color, width=5)
            draw.line((x + 70, 235, x + 140, 535), fill=line_color, width=5)
            draw.line((x + 25, 390, x + 115, 390), fill=electric, width=3)
        for y in (260, 340, 420):
            draw.line((80, y, 1200, y + ((variant % 19) - 9)), fill=(0, 85, 164, 45), width=2)
    else:
        for x in range(120, 1140, 135):
            h = 120 + ((x + variant) % 170)
            draw.rectangle((x, 560 - h, x + 82, 560), outline=line_color, width=4, fill=(255, 255, 255, 60))
        draw.line((80, 560, 1200, 560), fill=line_color, width=4)

    _draw_people(draw, width, height, line_color, node_color, electric, variant)


def _draw_people(draw: ImageDraw.ImageDraw, width: int, height: int, line_color: tuple[int, int, int, int], node_color: tuple[int, int, int, int], electric: tuple[int, int, int, int], variant: int) -> None:
    base = int(height * 0.69)
    positions = [210, 300, 405, 525]
    for idx, x in enumerate(positions):
        y = base - 45 + ((variant + idx * 11) % 22)
        draw.ellipse((x - 18, y - 72, x + 18, y - 36), fill=node_color)
        draw.rounded_rectangle((x - 26, y - 34, x + 26, y + 48), radius=12, fill=(0, 85, 164, 72), outline=line_color, width=2)
        draw.line((x - 20, y + 48, x - 42, y + 118), fill=line_color, width=5)
        draw.line((x + 20, y + 48, x + 42, y + 118), fill=line_color, width=5)
    draw.rounded_rectangle((155, base + 62, 590, base + 105), radius=14, outline=electric, width=3, fill=(255, 255, 255, 54))


def _draw_scene_overlay(draw: ImageDraw.ImageDraw, scene: str, width: int, height: int, line_color: tuple[int, int, int, int], node_color: tuple[int, int, int, int], electric: tuple[int, int, int, int], variant: int) -> None:
    ink = line_color
    fill = node_color
    accent = electric
    jitter = (variant % 31) - 15
    if scene == "boardroom":
        table_y = int(height * 0.48)
        draw.rounded_rectangle((150, table_y, 1040, table_y + 78), radius=18, outline=ink, width=4, fill=(255, 255, 255, 42))
        for i, x in enumerate([230, 390, 560, 730, 900]):
            y = table_y - 72 + (i % 2) * 18
            draw.ellipse((x - 24, y - 24, x + 24, y + 24), fill=fill)
            draw.line((x, y + 24, x + jitter, table_y - 4), fill=ink, width=4)
        draw.rectangle((505, table_y + 14, 690, table_y + 50), outline=accent, width=3)
    elif scene == "economics":
        base = int(height * 0.58)
        for i, x in enumerate(range(180, 1030, 120)):
            h = 70 + ((i * 37 + variant) % 210)
            draw.rectangle((x, base - h, x + 58, base), fill=(0, 166, 81, 94), outline=ink, width=2)
        draw.line((140, base, 1110, base), fill=ink, width=4)
        draw.line((170, base - 260, 1040, base - 80), fill=accent, width=5)
    elif scene == "policy":
        ground = int(height * 0.58)
        draw.rectangle((220, ground - 180, 980, ground - 130), outline=ink, width=4, fill=(255, 255, 255, 46))
        for x in range(280, 940, 110):
            draw.rectangle((x, ground - 130, x + 45, ground + 40), outline=ink, width=4)
        draw.polygon([(170, ground - 180), (600, ground - 300), (1030, ground - 180)], outline=ink, fill=(255, 255, 255, 32))
        draw.line((180, ground + 42, 1040, ground + 42), fill=accent, width=4)
    elif scene == "network":
        nodes = [(170, 405), (330, 285), (500, 365), (690, 230), (860, 350), (1040, 275), (1110, 430)]
        nodes = [(x, y + ((variant + i * 13) % 25) - 12) for i, (x, y) in enumerate(nodes)]
        for a, b in zip(nodes, nodes[1:]):
            draw.line((*a, *b), fill=ink, width=5)
        for x, y in nodes:
            draw.ellipse((x - 26, y - 26, x + 26, y + 26), fill=fill, outline=accent, width=3)
        draw.rounded_rectangle((410, 465, 770, 535), radius=15, outline=ink, width=4, fill=(255, 255, 255, 48))
    elif scene == "infrastructure":
        horizon = int(height * 0.54)
        for x in [180, 420, 670, 930]:
            draw.line((x, horizon + 90, x + 70, horizon - 170), fill=ink, width=5)
            draw.line((x + 70, horizon - 170, x + 140, horizon + 90), fill=ink, width=5)
            draw.line((x + 20, horizon - 20, x + 120, horizon - 20), fill=accent, width=3)
        for y in [horizon - 120, horizon - 70, horizon - 20]:
            draw.line((120, y, 1160, y + ((variant % 21) - 10)), fill=ink, width=2)
    elif scene == "dashboard":
        for i, (x, y) in enumerate([(150, 275), (430, 215), (710, 300), (960, 235)]):
            draw.rounded_rectangle((x, y, x + 210, y + 150), radius=18, outline=ink, width=4, fill=(255, 255, 255, 48))
            draw.line((x + 24, y + 105, x + 72, y + 70, x + 120, y + 88, x + 176, y + 44), fill=accent, width=5)
            draw.ellipse((x + 158, y + 28, x + 188, y + 58), fill=fill)
    elif scene == "evidence":
        for i, x in enumerate([160, 370, 580, 790, 1000]):
            y = 250 + (i % 2) * 55
            draw.rounded_rectangle((x, y, x + 145, y + 220), radius=16, outline=ink, width=4, fill=(255, 255, 255, 50))
            draw.line((x + 24, y + 56, x + 118, y + 56), fill=accent, width=5)
            draw.line((x + 24, y + 106, x + 100, y + 106), fill=ink, width=3)
            draw.line((x + 24, y + 150, x + 118, y + 150), fill=ink, width=3)


def _hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _shorten(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."


def _section_title_for_prompt(section: Dict[str, Any], idx: int) -> str:
    title = str(section.get("title") or "").strip()
    if _is_generic_section_title(title):
        lead = str(section.get("lead") or "").strip()
        if lead:
            return _shorten(lead, 90)
        paragraphs = section.get("paragraphs", []) or []
        if paragraphs:
            return _shorten(paragraphs[0], 90)
        return f"Section {idx} strategic visual"
    return title


def _is_generic_section_title(title: str) -> bool:
    return bool(__import__("re").match(r"^\s*(section|chapter)\s*\d+\s*$", str(title or ""), __import__("re").I))
