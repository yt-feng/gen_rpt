from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


LATEX_HEADER = r"""
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=13mm,top=11mm,bottom=12mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{tabularx}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{hyperref}
\defaultfontfeatures{Ligatures=TeX}
\IfFontExistsTF{Noto Sans CJK SC}{\setmainfont{Noto Sans CJK SC}\setsansfont{Noto Sans CJK SC}}{\setmainfont{DejaVu Sans}\setsansfont{DejaVu Sans}}
\definecolor{BOBlue}{HTML}{0055A4}
\definecolor{BOBright}{HTML}{3273F6}
\definecolor{BONavy}{HTML}{051C2C}
\definecolor{BOMuted}{HTML}{6F7F8F}
\definecolor{BOLine}{HTML}{DCE3EA}
\definecolor{BOLight}{HTML}{F4F8FC}
\definecolor{BOCell}{HTML}{EEF5FB}
\hypersetup{colorlinks=true,linkcolor=BOBlue,urlcolor=BOBlue}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.2pt}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.15}
\hyphenpenalty=10000
\exhyphenpenalty=10000
\tolerance=4000
\emergencystretch=2em
\sloppy
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\scriptsize\color{BOMuted} BLUEOCEAN | CONFIDENTIAL}
\fancyfoot[L]{\scriptsize\color{BOMuted} BlueOcean | Confidential}
\fancyfoot[R]{\scriptsize\color{BOMuted} \thepage}
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
    sections = _safe_sections(report.get("sections", []))
    summary = _summary_items(report.get("executive_summary", []))
    institutions = report.get("reference_institutions", []) or []

    parts: List[str] = [LATEX_HEADER, "\\begin{document}", "\\raggedright"]
    parts.append(_cover_page(title, subtitle, cover))
    parts.append(_summary_page(summary))
    parts.append(_strategic_logic_page(report, sections))
    parts.append(_contents_page(sections))
    parts.append(_disclaimer_page(institutions))
    for idx, section in enumerate(sections, start=1):
        parts.append(_section_block(section, assets, idx))
    if institutions:
        refs = _tex(", ".join(str(x) for x in institutions))
        parts.append("\\clearpage\n" + _kicker("Reference note") + f"{{\\small\\color{{BOMuted}} This report was informed by public research and data from: {refs}. Full source backup is archived in the backup folder.}}\n")
    parts.append("\\end{document}\n")
    return "\n".join(parts)


def _cover_page(title: str, subtitle: str, cover: str) -> str:
    background = ""
    if cover:
        background = f"\\node[anchor=south west,inner sep=0] at (current page.south west) {{\\includegraphics[width=\\paperwidth,height=\\paperheight]{{{cover}}}}};"
    return rf"""
\thispagestyle{{empty}}
\begin{{tikzpicture}}[remember picture,overlay]
{background}
\fill[white,opacity=.95] ([xshift=14mm,yshift=-17mm]current page.north west) rectangle ++(138mm,-75mm);
\fill[BOBright] ([xshift=14mm,yshift=-17mm]current page.north west) rectangle ++(138mm,-1.8mm);
\node[anchor=north west,text width=126mm] at ([xshift=21mm,yshift=-25mm]current page.north west) {{\sffamily\scriptsize\bfseries\color{{BOBlue}} BLUEOCEAN\\DEEP RESEARCH REPORT}};
\node[anchor=north west,text width=126mm] at ([xshift=21mm,yshift=-39mm]current page.north west) {{\parbox{{126mm}}{{\raggedright\sffamily\fontsize{{21}}{{23}}\selectfont\color{{BONavy}} {title}}}}};
\node[anchor=north west,text width=126mm] at ([xshift=21mm,yshift=-77mm]current page.north west) {{\sffamily\scriptsize\color{{BOMuted}} {subtitle}}};
\end{{tikzpicture}}
\clearpage
"""


def _summary_page(summary: List[str]) -> str:
    rows = []
    for idx, item in enumerate((summary or [])[:8], start=1):
        rows.append(f"\\textcolor{{BOBlue}}{{\\bfseries {idx:02d}}} & {{\\small {_tex(_shorten(item, 300))}}} \\\\[5pt]\n")
    if not rows:
        rows.append("\\textcolor{BOBlue}{\\bfseries 01} & {\\small Evidence base and management implications should be validated through source backup and client discussion.} \\\\[5pt]\n")
    return (
        _kicker("Key highlights")
        + _heading("The report opens with decision-relevant conclusions")
        + _rule()
        + "\\begin{tabularx}{\\linewidth}{p{12mm}Y}\n"
        + "".join(rows)
        + "\\end{tabularx}\n"
        + _mini_bar("Evidence", "Policy", "Execution", "Value")
        + "\\clearpage\n"
    )


def _strategic_logic_page(report: Dict[str, Any], sections: List[Dict[str, Any]]) -> str:
    thesis = _tex(_shorten(report.get("report_subtitle") or "Strategy should focus on where evidence, policy, ecosystem maturity and commercialization converge.", 260))
    labels = [_tex(_shorten(_strip_number_prefix(s.get("title", f"Pillar {idx}")), 54)) for idx, s in enumerate(sections[:4], start=1)]
    while len(labels) < 4:
        labels.append("Strategic pillar")
    return rf"""
{_kicker("Strategic logic")}
{_heading("The assessment links market evidence to management action")}
{_rule()}
{{\small {thesis}}}\par\vspace{{8pt}}
\begin{{center}}
\begin{{tikzpicture}}[x=1mm,y=1mm]
\tikzstyle{{nodebox}}=[draw=BOLine,fill=BOLight,rounded corners=2mm,minimum width=38mm,minimum height=17mm,align=center,text width=34mm]
\node[nodebox] (a) at (22,44) {{\textcolor{{BOBlue}}{{\bfseries Evidence}}\\\scriptsize {labels[0]}}};
\node[nodebox] (b) at (70,44) {{\textcolor{{BOBlue}}{{\bfseries Policy}}\\\scriptsize {labels[1]}}};
\node[nodebox] (c) at (118,44) {{\textcolor{{BOBlue}}{{\bfseries Ecosystem}}\\\scriptsize {labels[2]}}};
\node[nodebox] (d) at (166,44) {{\textcolor{{BOBlue}}{{\bfseries Action}}\\\scriptsize {labels[3]}}};
\draw[->,very thick,BOBright] (a) -- (b);
\draw[->,very thick,BOBright] (b) -- (c);
\draw[->,very thick,BOBright] (c) -- (d);
\node[draw=BOBlue,fill=white,rounded corners=2mm,align=center,text width=142mm,minimum height=17mm] at (94,14) {{\small Client-ready recommendation requires a clear bridge from factual signal to investable or actionable choice.}};
\end{{tikzpicture}}
\end{{center}}
\vspace{{4pt}}
\begin{{tabularx}}{{\linewidth}}{{p{{34mm}}YY}}
\textcolor{{BOBlue}}{{\bfseries Lens}} & \textcolor{{BOBlue}}{{\bfseries What it tests}} & \textcolor{{BOBlue}}{{\bfseries Output in the report}} \\
Market signal & Whether the opportunity is structurally supported or merely episodic & Conclusion-first chapters and prioritized management implications \\
Ecosystem maturity & Whether capabilities and partners can turn technology into deployment & Native tables, issue maps and risk screens embedded in LaTeX \\
Execution risk & Whether bottlenecks can change timing, valuation or partnership structure & Risk register and mitigation logic in later chapters \\
\end{{tabularx}}
\clearpage
"""


def _contents_page(sections: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, section in enumerate(sections, start=1):
        title = _tex(_strip_number_prefix(section.get("title", "Section")))
        rows.append(f"\\textcolor{{BOBlue}}{{\\bfseries {idx}}} & {title} \\\\[4pt]\n")
    if not rows:
        rows.append("\\textcolor{BOBlue}{\\bfseries 1} & Executive priorities and implications \\\\[4pt]\n")
    return (
        _kicker("Contents")
        + _heading("Contents")
        + _rule()
        + "\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n"
        + "".join(rows)
        + "\\end{tabularx}\n\\clearpage\n"
    )


def _disclaimer_page(institutions: List[Any]) -> str:
    ref_note = ""
    if institutions:
        ref_note = "\\vspace{6pt}{\\scriptsize\\color{BOMuted} This report was informed by public research and data from: " + _tex(", ".join(str(x) for x in institutions)) + ".}"
    return (
        _kicker("Disclaimer")
        + _heading("Disclaimer")
        + _rule()
        + "{\\small This document is a management consulting and research analysis deliverable for strategy discussion only and does not constitute investment, legal, tax, or audit advice.}\n"
        + ref_note
        + "\\vspace{6pt}\n{\\scriptsize\\color{BOMuted} The full source backup is archived in the backup folder.}\n\\clearpage\n"
    )


def _section_block(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get("title", f"Section {idx}")))
    lead = _tex(_shorten(section.get("lead", ""), 230))
    paragraphs = [_tex(p) for p in section.get("paragraphs", [])[:5]]
    while len(paragraphs) < 3:
        paragraphs.append("Evidence should be translated into management implications and validated against the source backup.")
    takeaways = [_tex(_shorten(x, 135)) for x in section.get("key_takeaways", [])[:3]]
    image_path = _resolve_image_path(section, assets, idx)
    image_block = _image_block(image_path)
    takeaway_text = " ".join([f"\\textbullet\\ {x}" for x in takeaways]) if takeaways else "\\textbullet\\ Validate assumptions with source backup. \\textbullet\\ Translate evidence into action."
    lead_block = f"{{\\textcolor{{BOBlue}}{{\\normalsize {lead}}}}}\\par\\vspace{{2pt}}\n" if lead else ""
    body_left = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs[:2]])
    body_right = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs[2:4]])
    continuation = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs[4:]])
    return (
        "\\clearpage\n"
        + _kicker(f"Chapter {idx}")
        + _heading(title)
        + lead_block
        + "\\begin{tabularx}{\\linewidth}{Y p{58mm}}\n"
        + body_left
        + " & "
        + image_block
        + " \\\n\\end{tabularx}\n"
        + "\\vspace{3pt}\\noindent\\fcolorbox{BOLine}{BOLight}{\\parbox{0.965\\linewidth}{\\small \\textbf{So what:} " + takeaway_text + "}}\\vspace{4pt}\n"
        + "{\\small " + body_right + "}\n"
        + _analysis_tool(idx)
        + (f"\n{{\\small {continuation}}}\n" if continuation else "")
    )


def _image_block(path: str) -> str:
    if not path:
        return _visual_placeholder()
    return f"\\includegraphics[width=58mm,height=45mm,keepaspectratio]{{{path}}}"


def _resolve_image_path(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    candidates = [f"image-{idx}", str(section.get("visual_hint", "")), "cover-background"]
    for key in candidates:
        value = assets.get(key, "")
        normalized = _asset_path(value)
        if normalized:
            return normalized
    return ""


def _visual_placeholder() -> str:
    return r"""
\begin{tikzpicture}[x=1mm,y=1mm]
\fill[BOLight] (0,0) rectangle (58,45);
\draw[BOLine] (0,0) rectangle (58,45);
\draw[BOBright,very thick] (8,12) -- (20,28) -- (34,18) -- (50,34);
\foreach \x/\y in {8/12,20/28,34/18,50/34}{\fill[BOBlue] (\x,\y) circle (1.8);}
\node[align=center,text width=48mm] at (29,8) {\scriptsize\color{BOMuted} Strategic visual};
\end{tikzpicture}
"""


def _analysis_tool(idx: int) -> str:
    tools = [_architecture_diagram, _timeline_diagram, _ecosystem_table, _application_matrix, _risk_register, _decision_table]
    return tools[(idx - 1) % len(tools)]()


def _architecture_diagram() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries DIAGNOSTIC ARCHITECTURE MAP}}\par
\begin{center}\begin{tikzpicture}[x=1mm,y=1mm]
\tikzstyle{box}=[draw=BOLine,fill=BOLight,rounded corners=2mm,minimum width=38mm,minimum height=14mm,align=center,text width=35mm]
\node[box] (a) at (25,22) {\small Hardware / platform};
\node[box] (b) at (73,22) {\small Funding / policy};
\node[box] (c) at (121,22) {\small Ecosystem / talent};
\node[box] (d) at (169,22) {\small Commercial use cases};
\draw[->,very thick,BOBright] (a) -- (b);\draw[->,very thick,BOBright] (b) -- (c);\draw[->,very thick,BOBright] (c) -- (d);
\end{tikzpicture}\end{center}
"""


def _timeline_diagram() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries INDICATIVE DEVELOPMENT ROADMAP}}\par
\begin{center}\begin{tikzpicture}[x=1mm,y=1mm]
\draw[very thick,BOBright] (10,18) -- (170,18);
\fill[BOBlue] (10,18) circle (2.1);\node[anchor=south,align=center,text width=32mm] at (10,22) {\scriptsize\textcolor{BOBlue}{\textbf{2021}}\\\scriptsize Policy launch};
\fill[BOBlue] (55,18) circle (2.1);\node[anchor=south,align=center,text width=32mm] at (55,22) {\scriptsize\textcolor{BOBlue}{\textbf{2023}}\\\scriptsize Pilot scaling};
\fill[BOBlue] (105,18) circle (2.1);\node[anchor=south,align=center,text width=32mm] at (105,22) {\scriptsize\textcolor{BOBlue}{\textbf{2026}}\\\scriptsize Commercial proof};
\fill[BOBlue] (150,18) circle (2.1);\node[anchor=south,align=center,text width=32mm] at (150,22) {\scriptsize\textcolor{BOBlue}{\textbf{2030}}\\\scriptsize Industrial adoption};
\end{tikzpicture}\end{center}
"""


def _ecosystem_table() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries ECOSYSTEM ROLE MAP}}\par
\begin{tabularx}{\linewidth}{p{33mm}YY}
\textcolor{BOBlue}{\bfseries Actor} & \textcolor{BOBlue}{\bfseries Role in scaling} & \textcolor{BOBlue}{\bfseries Management implication} \\
Government / labs & Anchor funding, standards and strategic direction & Track policy shifts and non-market funding priorities \\
Large platforms & Provide infrastructure credibility and customer reach & Use partnerships to accelerate validation and go-to-market \\
Startups & Specialize around algorithms, components and vertical applications & Prioritize teams with clear application roadmap and defensible talent \\
Customers & Convert pilots into repeatable deployment evidence & Focus on use cases with visible ROI and procurement urgency \\
\end{tabularx}
"""


def _application_matrix() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries APPLICATION ATTRACTIVENESS SCREEN}}\par
\begin{tabularx}{\linewidth}{p{36mm}p{27mm}p{27mm}Y}
\textcolor{BOBlue}{\bfseries Application} & \textcolor{BOBlue}{\bfseries Time to proof} & \textcolor{BOBlue}{\bfseries Value clarity} & \textcolor{BOBlue}{\bfseries Priority logic} \\
Optimization / logistics & Near term & Medium & Attractive where narrow advantage can be demonstrated with existing workflows \\
Finance / risk & Near term & High & Strong business sponsor, but deployment depends on reliability and governance \\
Drug / materials discovery & Medium term & High & Strategic upside is large, but technical proof and data integration remain demanding \\
Security / infrastructure & Medium term & Medium & Policy-driven demand can support early deployment before broad commercial ROI \\
\end{tabularx}
"""


def _risk_register() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries RISK REGISTER AND MITIGATION LOGIC}}\par
\begin{tabularx}{\linewidth}{p{36mm}p{25mm}Y}
\textcolor{BOBlue}{\bfseries Risk} & \textcolor{BOBlue}{\bfseries Severity} & \textcolor{BOBlue}{\bfseries Mitigation lens} \\
Technology bottleneck & High & Stage-gate investment against measurable performance milestones \\
Supply chain constraint & Medium-high & Build local alternatives, dual-source critical components and monitor export controls \\
Talent gap & Medium & Back teams with credible academic pipelines and retention model \\
Commercial adoption delay & Medium & Prioritize customers with urgent pain points and clear procurement sponsor \\
\end{tabularx}
"""


def _decision_table() -> str:
    return r"""
\vspace{4pt}{\textcolor{BOBlue}{\scriptsize\bfseries DECISION FILTER FOR MANAGEMENT ACTION}}\par
\begin{tabularx}{\linewidth}{p{34mm}YY}
\textcolor{BOBlue}{\bfseries Decision test} & \textcolor{BOBlue}{\bfseries What good looks like} & \textcolor{BOBlue}{\bfseries Warning sign} \\
Strategic fit & Reinforces a priority market, capability or policy-backed growth lane & Attractive technology without a buyer or owner \\
Evidence quality & Demonstrated performance data, reference customers and transparent assumptions & Claims depend on one-off demos or unsupported forecasts \\
Scalability & Repeatable economics, supply access and partner ecosystem & Deployment depends on bespoke lab conditions \\
Timing & Clear 12-36 month proof path & Value creation pushed beyond practical investment horizon \\
\end{tabularx}
"""


def _mini_bar(a: str, b: str, c: str, d: str) -> str:
    return rf"""
\vspace{{8pt}}
\begin{{center}}
\begin{{tikzpicture}}[x=1mm,y=1mm]
\foreach \x/\w/\label in {{0/43/{a},47/35/{b},86/31/{c},121/27/{d}}}{{
  \fill[BOLight] (\x,0) rectangle +(36,8);
  \fill[BOBright] (\x,0) rectangle +(\w/2.1,8);
  \node[anchor=west] at (\x,12) {{\scriptsize\color{{BOMuted}} \label}};
}}
\end{{tikzpicture}}
\end{{center}}
"""


def _kicker(text: str) -> str:
    return "{\\textcolor{BOBlue}{\\scriptsize\\bfseries " + _tex(str(text).upper()) + "}}\\par\\vspace{-1pt}\n"


def _heading(text: str) -> str:
    return "{\\Large\\sffamily\\bfseries\\color{BONavy} " + text + "}\\par\\vspace{4pt}\n"


def _rule() -> str:
    return "\\vspace{2pt}{\\color{BOBright}\\rule{\\linewidth}{1pt}}\\vspace{5pt}\n"


def _asset_path(path: str, *, allow_svg: bool = True) -> str:
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    if not allow_svg and normalized.lower().endswith(".svg"):
        return ""
    if normalized.lower().endswith(".svg"):
        return ""
    return normalized


def _safe_sections(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list) and value:
        return [x if isinstance(x, dict) else {"title": str(x), "paragraphs": [str(x)]} for x in value]
    return [{"title": "Executive priorities and implications", "lead": "The analysis should be translated into a concise management agenda.", "paragraphs": ["The available evidence should be organized around decision quality, execution risk and near-term management implications.", "The most useful output is a short list of actions that can be tested against public evidence and client constraints.", "Follow-up work should validate the assumptions against the source backup."], "key_takeaways": ["Focus on actionability.", "Validate source quality.", "Translate evidence into decisions."], "visual_hint": "image-1"}]


def _summary_items(value: Any) -> List[str]:
    raw = [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else ([str(value).strip()] if str(value).strip() else [])
    if len(raw) <= 2 and raw and len(" ".join(raw)) > 450:
        raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(raw)) if len(s.strip()) > 20]
    return raw[:8]


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", str(text or "")).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."


def _tex(value: Any) -> str:
    text = str(value or "").replace("\u00ad", "").replace("\ufffe", "").replace("\ufeff", "")
    text = " ".join(text.replace("\n", " ").split())
    mapping = {
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
    return "".join(mapping.get(ch, ch) for ch in text)
