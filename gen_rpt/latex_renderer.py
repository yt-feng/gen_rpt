from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


LATEX_HEADER = r"""
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=14mm,top=12mm,bottom=13mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{tabularx}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{hyperref}
\defaultfontfeatures{Ligatures=TeX}
\setmainfont{DejaVu Sans}
\setsansfont{DejaVu Sans}
\definecolor{BOBlue}{HTML}{0055A4}
\definecolor{BOBright}{HTML}{3273F6}
\definecolor{BONavy}{HTML}{051C2C}
\definecolor{BOMuted}{HTML}{6F7F8F}
\definecolor{BOLine}{HTML}{DCE3EA}
\definecolor{BOLight}{HTML}{F4F8FC}
\hypersetup{colorlinks=true,linkcolor=BOBlue,urlcolor=BOBlue}
\setlength{\parindent}{0pt}
\setlength{\parskip}{4pt}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\scriptsize\color{BOMuted} BLUEOCEAN | CONFIDENTIAL}
\fancyfoot[L]{\scriptsize\color{BOMuted} BlueOcean | Confidential}
\fancyfoot[R]{\scriptsize\color{BOMuted} \thepage}
\newcommand{\bohrule}{\vspace{2pt}{\color{BOBright}\rule{\linewidth}{1pt}}\vspace{5pt}}
\newcommand{\kicker}[1]{\textcolor{BOBlue}{\scriptsize\bfseries\MakeUppercase{#1}}\\[-2pt]}
\newcommand{\lead}[1]{\textcolor{BOBlue}{\normalsize #1}\par\vspace{2pt}}
\newcommand{\source}[1]{\textcolor{BOMuted}{\scriptsize #1}}
\newcommand{\takeaway}[1]{\vspace{3pt}\noindent\colorbox{BOLight}{\parbox{0.965\linewidth}{\small #1}}\vspace{3pt}}
\newcommand{\Needspace}[1]{\par\vspace{2pt}}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
"""


def render_latex_pdf(report: Dict[str, Any], assets: Dict[str, str], output_dir: Path, topic: str, language: str = "en") -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path = output_dir / "report_latex.tex"
    pdf_path = output_dir / "report_latex.pdf"
    tex_path.write_text(_build_tex(report, assets, output_dir, topic, language), encoding="utf-8")

    xelatex = shutil.which("xelatex")
    if not xelatex:
        (output_dir / "latex_error.txt").write_text("xelatex not found; report_latex.tex was generated but not compiled.\n", encoding="utf-8")
        return {"tex_path": str(tex_path), "pdf_path": ""}

    try:
        combined_output = ""
        for _ in range(2):
            run = subprocess.run(
                [xelatex, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
                cwd=str(output_dir),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
            )
            combined_output += run.stdout[-5000:]
        (output_dir / "latex_build.log").write_text(combined_output, encoding="utf-8")
        return {"tex_path": str(tex_path), "pdf_path": str(pdf_path) if pdf_path.exists() else ""}
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or ""
        (output_dir / "latex_error.txt").write_text(output[-8000:] or str(exc), encoding="utf-8")
        return {"tex_path": str(tex_path), "pdf_path": ""}
    except Exception as exc:
        (output_dir / "latex_error.txt").write_text(str(exc), encoding="utf-8")
        return {"tex_path": str(tex_path), "pdf_path": ""}


def _build_tex(report: Dict[str, Any], assets: Dict[str, str], output_dir: Path, topic: str, language: str) -> str:
    title = _tex(report.get("report_title") or topic)
    subtitle = _tex(report.get("report_subtitle") or "Strategic assessment")
    cover = _asset_path(assets.get("cover-background", ""))
    logo = _asset_path(assets.get("brand-logo", ""), allow_svg=False)
    sections = report.get("sections", []) or []
    summary = [_tex(x) for x in report.get("executive_summary", [])[:8]]
    institutions = report.get("reference_institutions", []) or []

    parts: List[str] = [LATEX_HEADER, "\\begin{document}"]
    parts.append(_cover_page(title, subtitle, cover, logo))
    parts.append(_summary_page(summary))
    parts.append(_contents_page(sections))
    parts.append(_disclaimer_page(institutions))

    for idx, section in enumerate(sections, start=1):
        parts.append(_section_block(section, assets, idx))

    if institutions:
        refs = _tex(", ".join(str(x) for x in institutions))
        parts.append(f"\\Needspace{{30mm}}\n\\kicker{{Reference note}}\n{{\\small\\color{{BOMuted}} This report was informed by public research and data from: {refs}. Full source backup is archived in the backup folder.}}\n")

    parts.append("\\end{document}\n")
    return "\n".join(parts)


def _cover_page(title: str, subtitle: str, cover: str, logo: str) -> str:
    background = ""
    if cover:
        background = f"\\node[anchor=south west,inner sep=0] at (current page.south west) {{\\includegraphics[width=\\paperwidth,height=\\paperheight]{{{cover}}}}};"
    logo_node = ""
    if logo:
        logo_node = f"\\node[anchor=north east] at ([xshift=-13mm,yshift=-10mm]current page.north east) {{\\includegraphics[width=18mm]{{{logo}}}}};"
    return rf"""
\thispagestyle{{empty}}
\begin{{tikzpicture}}[remember picture,overlay]
{background}
\fill[white,opacity=.94] ([xshift=16mm,yshift=-18mm]current page.north west) rectangle ++(116mm,-65mm);
\fill[BOBright] ([xshift=16mm,yshift=-18mm]current page.north west) rectangle ++(116mm,-1.7mm);
{logo_node}
\node[anchor=north west,text width=104mm] at ([xshift=21mm,yshift=-25mm]current page.north west) {{\sffamily\scriptsize\bfseries\color{{BOBlue}} BLUEOCEAN\\DEEP RESEARCH REPORT}};
\node[anchor=north west,text width=104mm] at ([xshift=21mm,yshift=-37mm]current page.north west) {{\sffamily\Huge\color{{BONavy}} {title}}};
\node[anchor=north west,text width=104mm] at ([xshift=21mm,yshift=-69mm]current page.north west) {{\sffamily\scriptsize\color{{BOMuted}} {subtitle}}};
\end{{tikzpicture}}
\clearpage
"""


def _summary_page(summary: List[str]) -> str:
    rows = []
    for idx, item in enumerate(summary, start=1):
        rows.append(f"\\textcolor{{BOBlue}}{{\\bfseries {idx:02d}}} & {item} \\\\[5pt]\n")
    if not rows:
        rows.append("\\textcolor{BOBlue}{\\bfseries 01} & No executive summary was generated. \\\\\n")
    return rf"""
\kicker{{Key highlights}}
{{\Large\sffamily\bfseries\color{{BONavy}} The analysis points to a focused set of management priorities}}\\[4pt]
\bohrule
\begin{{tabularx}}{{\linewidth}}{{p{{12mm}}Y}}
{''.join(rows)}\end{{tabularx}}
\clearpage
"""


def _contents_page(sections: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, section in enumerate(sections, start=1):
        rows.append(f"\\textcolor{{BOBlue}}{{\\bfseries {idx}}} & {_tex(_strip_number_prefix(section.get('title', 'Section')))} \\\\[4pt]\n")
    return rf"""
\kicker{{Contents}}
{{\Large\sffamily\bfseries\color{{BONavy}} Contents}}\\[4pt]
\bohrule
\begin{{tabularx}}{{\linewidth}}{{p{{10mm}}Y}}
{''.join(rows)}\end{{tabularx}}
\clearpage
"""


def _disclaimer_page(institutions: List[Any]) -> str:
    ref_note = ""
    if institutions:
        ref_note = "\\vspace{6pt}\\source{This report was informed by public research and data from: " + _tex(", ".join(str(x) for x in institutions)) + ".}"
    return rf"""
\kicker{{Disclaimer}}
{{\Large\sffamily\bfseries\color{{BONavy}} Disclaimer}}\\[4pt]
\bohrule
{{\small This document is a management consulting and research analysis deliverable for strategy discussion only and does not constitute investment, legal, tax, or audit advice.}}
{ref_note}
\vspace{{6pt}}
\source{{The full source backup is archived in the backup folder.}}
\clearpage
"""


def _section_block(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get("title", f"Section {idx}")))
    lead = _tex(section.get("lead", ""))
    paragraphs = [_tex(p) for p in section.get("paragraphs", [])[:5]]
    takeaways = [_tex(x) for x in section.get("key_takeaways", [])[:3]]
    visual_key = str(section.get("visual_hint", ""))
    visual = _asset_path(assets.get(visual_key, ""))
    visual_block = ""
    if visual and visual_key.startswith("chart-"):
        visual_block = f"\\vspace{{3pt}}\\begin{{center}}\\includegraphics[width=.78\\linewidth,height=68mm,keepaspectratio]{{{visual}}}\\end{{center}}"
    elif visual and visual_key.startswith("image-"):
        visual_block = f"\\vspace{{3pt}}\\begin{{center}}\\includegraphics[width=.62\\linewidth,height=60mm,keepaspectratio]{{{visual}}}\\end{{center}}"

    takeaway_block = ""
    if takeaways:
        items = "".join([f"\\item {x}" for x in takeaways])
        takeaway_block = f"\\takeaway{{\\textbf{{Takeaways}}\\begin{{itemize}}{items}\\end{{itemize}}}}"

    paras = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs])
    lead_block = f"\\lead{{{lead}}}" if lead else ""
    return rf"""
\Needspace{{55mm}}
\kicker{{Chapter {idx}}}
{{\Large\sffamily\bfseries\color{{BONavy}} {title}}}\\[4pt]
{lead_block}
{paras}
{takeaway_block}
{visual_block}
"""


def _asset_path(path: str, *, allow_svg: bool = True) -> str:
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    if not allow_svg and normalized.lower().endswith(".svg"):
        return ""
    return normalized


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", str(text or "")).strip()


def _tex(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text
