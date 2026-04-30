from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


DEFAULT_THEME: Dict[str, Any] = {
    "brand_name": "BO Institute Strategy Agent",
    "report_label": "Deep Research Report",
    "palette": {
        "accent": "#0E6B72",
        "accent_dark": "#0A4F56",
        "ink": "#1F2937",
        "subtle": "#667085",
        "grid": "#D8DEE5",
        "paper": "#FFFFFF",
        "panel": "#F7F9FB",
        "line": "#DCE6EC",
    },
    "series_colors": ["#0E6B72", "#233645", "#7E96A8", "#C4D0D8"],
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
