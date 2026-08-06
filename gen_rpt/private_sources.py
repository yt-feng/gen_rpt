from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple
from urllib.parse import quote
from xml.etree import ElementTree

import fitz
from bs4 import BeautifulSoup

from .web_fetch import SourceDocument


SOURCE_MODES = ("web_only", "collection_only", "web_and_collection")
SUPPORTED_SOURCE_SUFFIXES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
}

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DEFAULT_MAX_CHARS = 40_000
_DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
_MAX_OPENXML_MEMBER_BYTES = 20 * 1024 * 1024
_MAX_OPENXML_TOTAL_BYTES = 40 * 1024 * 1024


def normalize_source_mode(value: str | None) -> str:
    mode = str(value or "web_only").strip().lower()
    if mode not in SOURCE_MODES:
        choices = ", ".join(SOURCE_MODES)
        raise ValueError(f"Unsupported source mode {value!r}. Expected one of: {choices}.")
    return mode


def combine_source_documents(
    web_sources: Sequence[SourceDocument] | None,
    private_sources: Sequence[SourceDocument] | None,
    source_mode: str,
) -> List[SourceDocument]:
    """Select and merge sources while keeping private documents first.

    Private documents lead in the combined mode because report synthesis only
    embeds a bounded number of full source excerpts in its prompt.
    """

    mode = normalize_source_mode(source_mode)
    web = list(web_sources or [])
    private = list(private_sources or [])
    if mode == "web_only":
        selected = web
    elif mode == "collection_only":
        selected = private
    else:
        selected = private + web
    return _dedupe_sources(selected)


def load_private_sources(
    source_dir: str | Path,
    *,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS,
) -> List[SourceDocument]:
    """Read supported documents below ``source_dir`` into SourceDocument rows.

    Paths embedded in the returned records are collection-relative
    ``private://`` URLs; host filesystem paths are not persisted in report
    artifacts.
    """

    root = Path(source_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Private source directory does not exist: {source_dir}")
    if not root.is_dir():
        raise ValueError(f"Private source path is not a directory: {source_dir}")
    if max_chars_per_file <= 0:
        raise ValueError("max_chars_per_file must be positive")

    max_file_bytes = _positive_int_env(
        "GEN_RPT_PRIVATE_MAX_FILE_BYTES",
        _DEFAULT_MAX_FILE_BYTES,
    )
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
            and not _is_hidden_relative_path(path, root)
        ),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )

    sources: List[SourceDocument] = []
    for path in candidates:
        relative_path = path.relative_to(root)
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            _log(f"skipped path outside source directory | file={relative_path.as_posix()!r}")
            continue
        try:
            if resolved.stat().st_size > max_file_bytes:
                _log(
                    "skipped oversized private source "
                    f"| file={relative_path.as_posix()!r} | max_bytes={max_file_bytes}"
                )
                continue
            content, embedded_title = _extract_document(resolved, max_chars=max_chars_per_file)
        except Exception as exc:
            _log(
                "skipped unreadable private source "
                f"| file={relative_path.as_posix()!r} | reason={str(exc)[:180]!r}"
            )
            continue

        content = _clean_content(content, max_chars=max_chars_per_file)
        if not content:
            _log(f"skipped empty private source | file={relative_path.as_posix()!r}")
            continue

        suffix = resolved.suffix.lower()
        relative_url = quote(relative_path.as_posix(), safe="/")
        title = _infer_title(resolved, content, embedded_title)
        sources.append(
            SourceDocument(
                title=title,
                url=f"private://private.collection/{relative_url}",
                query=f"Private source collection: {relative_path.as_posix()}",
                snippet=_snippet(content),
                content=content,
                source_type=suffix.lstrip("."),
                content_type=SUPPORTED_SOURCE_SUFFIXES[suffix],
                domain="private.collection",
            )
        )

    _log(f"private source collection loaded | directory={root.name!r} | sources={len(sources)}")
    return sources


def _extract_document(path: Path, *, max_chars: int) -> Tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path, max_chars=max_chars)
    if suffix == ".docx":
        return _extract_docx(path), ""
    if suffix == ".pptx":
        return _extract_pptx(path), ""
    if suffix in {".html", ".htm"}:
        return _extract_html(path)
    return _read_text(path), ""


def _extract_pdf(path: Path, *, max_chars: int) -> Tuple[str, str]:
    chunks: List[str] = []
    total = 0
    with fitz.open(str(path)) as document:
        metadata = document.metadata or {}
        title = str(metadata.get("title") or "").strip()
        for page in document:
            text = page.get_text("text") or ""
            if text.strip():
                chunks.append(text)
                total += len(text)
            if total >= max_chars:
                break
    return "\n\n".join(chunks), title


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        members = ["word/document.xml"]
        members.extend(
            sorted(
                name
                for name in names
                if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            )
        )
        _validate_openxml_members(archive, [member for member in members if member in names])
        paragraphs: List[str] = []
        for member in members:
            if member not in names:
                continue
            root = ElementTree.fromstring(archive.read(member))
            for paragraph in root.iter(f"{{{_WORD_NS}}}p"):
                text = "".join(
                    node.text or ""
                    for node in paragraph.iter(f"{{{_WORD_NS}}}t")
                ).strip()
                if text:
                    paragraphs.append(text)
        return "\n\n".join(paragraphs)


def _extract_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        _validate_openxml_members(archive, slide_names)
        slides: List[str] = []
        for index, member in enumerate(slide_names, start=1):
            root = ElementTree.fromstring(archive.read(member))
            lines = [
                (node.text or "").strip()
                for node in root.iter(f"{{{_DRAWING_NS}}}t")
                if (node.text or "").strip()
            ]
            if lines:
                slides.append(f"Slide {index}\n" + "\n".join(lines))
        return "\n\n".join(slides)


def _extract_html(path: Path) -> Tuple[str, str]:
    raw = _read_text(path)
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    container = soup.body or soup
    return container.get_text("\n", strip=True), title


MOJIBAKE_PATTERNS = ("Ã©", "â€", "Â·", "Ã¼", "Ã¤", "Ã¶", "ÃŸ", "Ã ", "Ã¡", "Ã¢", "Ã£", "Ã§", "Ã¨", "Ãª", "Ã¬", "Ã­", "Ã®", "Ã¯", "Ã±", "Ã²", "Ã³", "Ã´", "Ãµ", "Ã¹", "Ãº", "Ã»")


def is_corrupted_text(text: str, threshold: float = 0.02) -> bool:
    if not text:
        return False
    replacement_count = text.count("\ufffd")
    mojibake_count = sum(text.count(pat) for pat in MOJIBAKE_PATTERNS)
    total_bad = replacement_count + mojibake_count
    return (total_bad / max(1, len(text))) > threshold


def _read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except Exception:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(encoding, errors="strict")
            if not is_corrupted_text(text, 0.02):
                return text
        except (UnicodeDecodeError, LookupError):
            continue
    fallback = raw.decode("utf-8", errors="replace")
    if is_corrupted_text(fallback, 0.02):
        _log(f"quarantined corrupted private source text | file={path.name}")
        return ""
    return fallback



def _clean_content(value: str, *, max_chars: int) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars].rstrip()


def _infer_title(path: Path, content: str, preferred: str = "") -> str:
    if preferred.strip():
        return preferred.strip()[:240]
    markdown_heading = re.search(r"^#{1,6}\s+(.+)$", content, re.MULTILINE)
    if markdown_heading:
        return markdown_heading.group(1).strip()[:240]
    for line in content.splitlines():
        candidate = re.sub(r"\s+", " ", line).strip(" #-\t")
        if 3 <= len(candidate) <= 180 and not candidate.lower().startswith("slide "):
            return candidate
    return path.stem.replace("_", " ").replace("-", " ").strip()[:240] or "Private source"


def _snippet(content: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", content).strip()[:limit]


def _slide_number(value: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", value)
    return int(match.group(1)) if match else 0


def _validate_openxml_members(archive: zipfile.ZipFile, members: Sequence[str]) -> None:
    total_size = 0
    for member in members:
        size = archive.getinfo(member).file_size
        if size > _MAX_OPENXML_MEMBER_BYTES:
            raise ValueError(f"OpenXML member is too large: {member}")
        total_size += size
        if total_size > _MAX_OPENXML_TOTAL_BYTES:
            raise ValueError("OpenXML document expands beyond the supported size")


def _dedupe_sources(sources: Iterable[SourceDocument]) -> List[SourceDocument]:
    output: List[SourceDocument] = []
    seen: set[str] = set()
    for source in sources:
        url_key = str(source.url or "").strip().lower()
        fallback_key = "\x1f".join(
            [
                str(source.title or "").strip().lower(),
                str(source.content or "").strip()[:500],
            ]
        )
        key = url_key or fallback_key
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def _is_hidden_relative_path(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _positive_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _log(message: str) -> None:
    print(f"[gen_rpt.private] {message}", flush=True)
