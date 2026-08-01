from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


DEFAULT_THEME: Dict[str, Any] = {
    "brand_name": "GateX",
    "report_label": "Executive Intelligence Report",
    "palette": {
        "accent": "#176DDC",
        "accent_dark": "#061B46",
        "ink": "#17233A",
        "subtle": "#5C6F88",
        "grid": "#D7E2EF",
        "paper": "#FFFFFF",
        "panel": "#F3F7FC",
        "line": "#CBD8E8",
    },
    "series_colors": ["#176DDC", "#061B46", "#7893AF", "#D7E2EF"],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_theme() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "branding" / "theme.json",
        repo_root / "theme.json",
    ]
    theme = deepcopy(DEFAULT_THEME)
    for path in candidates:
        if path.exists():
            try:
                user_theme = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(user_theme, dict):
                    theme = _deep_merge(theme, user_theme)
                    break
            except Exception:
                continue
    return theme
