from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def render_pdf_from_html(html_path: Path, pdf_path: Path) -> Path:
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    binary = shutil.which("wkhtmltopdf")
    if not binary:
        raise RuntimeError(
            "wkhtmltopdf is not installed. Install it locally or run the GitHub Action workflow, which installs it automatically."
        )

    command = [
        binary,
        "--enable-local-file-access",
        "--encoding",
        "utf-8",
        "--print-media-type",
        "--margin-top",
        "12",
        "--margin-right",
        "12",
        "--margin-bottom",
        "12",
        "--margin-left",
        "12",
        str(html_path),
        str(pdf_path),
    ]
    subprocess.run(command, check=True, cwd=str(html_path.parent))
    return pdf_path
