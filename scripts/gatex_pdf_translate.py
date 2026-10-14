"""Private single-document entry point for the GateX PDF localization engine."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from gen_rpt.gatex_pdf_localization import LocalizationError, digest, translate_pdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--qa", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    progress = json.loads(args.progress.read_text()) if args.progress.exists() else None

    def save(value):
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.progress.with_suffix(args.progress.suffix + ".partial")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(args.progress)

    try:
        result = translate_pdf(args.source, args.output, args.source_sha256 or digest(args.source.read_bytes()),
                               progress=progress, save_progress=save, force=args.force)
        args.qa.parent.mkdir(parents=True, exist_ok=True)
        args.qa.write_text(json.dumps(result["qa"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: result[key] for key in ("sha256", "pageCount", "byteSize")}))
    except LocalizationError as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
