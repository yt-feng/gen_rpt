from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from .theme import load_theme


THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]


def copy_or_generate_brand_assets(output_assets_dir: Path) -> Dict[str, str]:
    output_assets_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]

    logo_src = repo_root / "branding" / "logo.svg"
    logo_target = output_assets_dir / "brand-logo.svg"
    if logo_src.exists():
        shutil.copyfile(logo_src, logo_target)

    custom_cover = None
    for name in ["cover_background.png", "cover_background.jpg", "cover_background.jpeg", "cover_background.webp"]:
        candidate = repo_root / "branding" / name
        if candidate.exists():
            custom_cover = candidate
            break

    cover_target = output_assets_dir / "cover-background.png"
    if custom_cover is not None:
        shutil.copyfile(custom_cover, cover_target)
    else:
        generate_cover_background(cover_target)

    return {
        "brand-logo": f"assets/{logo_target.name}",
        "cover-background": f"assets/{cover_target.name}",
    }


def generate_cover_background(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(12, 16), dpi=180)
    ax = plt.axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    width = 1200
    height = 1600
    import numpy as np

    y = np.linspace(0, 1, height).reshape(-1, 1)
    x = np.linspace(0, 1, width).reshape(1, -1)

    c1 = np.array([9, 28, 54]) / 255.0
    c2 = np.array([12, 74, 100]) / 255.0
    c3 = np.array([46, 125, 151]) / 255.0
    gradient = ((1 - y)[:, :, None] * c1) + (y[:, :, None] * c2)
    glow = np.exp(-((x - 0.78) ** 2 / 0.02 + (y - 0.18) ** 2 / 0.03))[:, :, None]
    band = np.exp(-((x - 0.24) ** 2 / 0.08 + (y - 0.75) ** 2 / 0.08))[:, :, None]
    image = gradient + glow * (c3 * 0.9) + band * (np.array([120, 170, 188]) / 255.0 * 0.35)
    image = np.clip(image, 0, 1)
    ax.imshow(image, extent=[0, 1, 0, 1], origin="lower")

    circles = [
        (0.18, 0.82, 0.22, (0.16, 0.44, 0.58, 0.22)),
        (0.88, 0.10, 0.28, (0.19, 0.57, 0.63, 0.18)),
        (0.70, 0.72, 0.14, (0.45, 0.78, 0.82, 0.12)),
    ]
    for cx, cy, r, color in circles:
        ax.add_patch(Circle((cx, cy), r, color=color, ec=None))

    for idx in range(10):
        t = idx / 9
        xs = np.linspace(-0.1, 1.05, 200)
        ys = 0.14 + 0.72 * t + 0.02 * np.sin(2 * math.pi * (xs * 1.4 + t * 0.7))
        ax.plot(xs, ys, color=(0.75, 0.92, 0.96, 0.08 if idx < 7 else 0.12), linewidth=1.8)

    plt.savefig(output_path, bbox_inches="tight", pad_inches=0, facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def summarize_reference_institutions(references: List[Dict], sources: List[Dict]) -> List[str]:
    names = []
    for item in references:
        url = item.get("url", "")
        title = item.get("title", "")
        name = _institution_from_url(url) or _institution_from_title(title)
        if name:
            names.append(name)
    for item in sources:
        url = item.get("url", "")
        title = item.get("title", "")
        name = _institution_from_url(url) or _institution_from_title(title)
        if name:
            names.append(name)

    deduped = []
    seen = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped[:8]


def write_reference_backup(output_dir: Path, references: List[Dict], sources: List[Dict]) -> Path:
    backup_dir = output_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    summary_lines = ["# Reference backup", ""]
    for idx, ref in enumerate(references, start=1):
        summary_lines.append(f"## Ref {idx}")
        summary_lines.append(f"- Title: {ref.get('title', '')}")
        summary_lines.append(f"- URL: {ref.get('url', '')}")
        summary_lines.append(f"- Note: {ref.get('note', '')}")
        summary_lines.append("")
    (backup_dir / "reference_notes.md").write_text("\n".join(summary_lines).strip() + "\n", encoding="utf-8")

    for idx, source in enumerate(sources, start=1):
        lines = [
            f"Title: {source.get('title', '')}",
            f"URL: {source.get('url', '')}",
            f"Search Query: {source.get('query', '')}",
            "",
            source.get('content', ''),
        ]
        (backup_dir / f"source_{idx:02d}.txt").write_text("\n".join(lines), encoding="utf-8")

    return backup_dir


def _institution_from_url(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    host = host.replace("www.", "")
    mapping = {
        "mckinsey.com": "McKinsey",
        "goldmansachs.com": "Goldman Sachs",
        "weforum.org": "World Economic Forum",
        "worldbank.org": "World Bank",
        "imf.org": "IMF",
        "oecd.org": "OECD",
        "statista.com": "Statista",
        "bcg.com": "BCG",
        "bain.com": "Bain & Company",
        "gs.com": "Goldman Sachs",
        "openai.com": "OpenAI",
        "deepseek.com": "DeepSeek",
        "morganstanley.com": "Morgan Stanley",
        "pwc.com": "PwC",
        "deloitte.com": "Deloitte",
        "ey.com": "EY",
        "kpmg.com": "KPMG",
    }
    for domain, label in mapping.items():
        if host.endswith(domain):
            return label
    parts = host.split(".")
    if len(parts) >= 2:
        root = parts[-2]
        if root:
            return root.replace("-", " ").title()
    return ""


def _institution_from_title(title: str) -> str:
    text = str(title)
    keywords = [
        "McKinsey",
        "Goldman Sachs",
        "World Economic Forum",
        "World Bank",
        "IMF",
        "OECD",
        "Statista",
        "BCG",
        "Bain",
        "OpenAI",
        "DeepSeek",
        "Morgan Stanley",
        "PwC",
        "Deloitte",
        "EY",
        "KPMG",
    ]
    for key in keywords:
        if key.lower() in text.lower():
            return key
    return ""
