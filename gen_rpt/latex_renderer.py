from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


LATEX_HEADER = r"""
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=13.5mm,top=11.5mm,bottom=12.5mm]{geometry}
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
\definecolor{BOMid}{HTML}{DDEAF7}
\definecolor{BOGreen}{HTML}{3A8B84}
\hypersetup{colorlinks=true,linkcolor=BOBlue,urlcolor=BOBlue}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.6pt}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.18}
\hyphenpenalty=10000
\exhyphenpenalty=10000
\tolerance=3000
\emergencystretch=2em
\sloppy
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\scriptsize\color{BOMuted} BLUEOCEAN | CONFIDENTIAL}
\fancyfoot[L]{\scriptsize\color{BOMuted} BlueOcean | Confidential}
\fancyfoot[R]{\scriptsize\color{BOMuted} \thepage}
\newcommand{\bohrule}{\vspace{2pt}{\color{BOBright}\rule{\linewidth}{1pt}}\vspace{5pt}}
\newcommand{\kicker}[1]{\textcolor{BOBlue}{\scriptsize\bfseries\MakeUppercase{#1}}\\[-1pt]}
\newcommand{\lead}[1]{\textcolor{BOBlue}{\normalsize #1}\par\vspace{2pt}}
\newcommand{\source}[1]{\textcolor{BOMuted}{\scriptsize #1}}
\newcommand{\takeaway}[1]{\vspace{4pt}\noindent\colorbox{BOLight}{\parbox{0.965\linewidth}{\small #1}}\vspace{3pt}}
\newcommand{\boxtitle}[1]{\textcolor{BOBlue}{\scriptsize\bfseries\MakeUppercase{#1}}}
\newcommand{\Needspace}[1]{\par\vspace{2pt}}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
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
    summary = _summary_items(report.get("executive_summary", []))
    institutions = report.get("reference_institutions", []) or []

    parts: List[str] = [LATEX_HEADER, "\\begin{document}", "\\raggedright"]
    parts.append(_cover_page(title, subtitle, cover, logo))
    parts.append(_summary_page(summary))
    parts.append(_strategic_logic_page(report, sections))
    parts.append(_contents_page(sections))
    parts.append(_disclaimer_page(institutions))

    for idx, section in enumerate(sections, start=1):
        parts.append(_section_block(section, assets, idx))

    if institutions:
        refs = _tex(", ".join(str(x) for x in institutions))
        parts.append(f"\\clearpage\n\\kicker{{Reference note}}\n{{\\small\\color{{BOMuted}} This report was informed by public research and data from: {refs}. Full source backup is archived in the backup folder.}}\n")

    parts.append("\\end{document}\n")
    return "\n".join(parts)


def _cover_page(title: str, subtitle: str, cover: str, logo: str) -> str:
    background = ""
    if cover:
        background = f"\\node[anchor=south west,inner sep=0] at (current page.south west) {{\\includegraphics[width=\\paperwidth,height=\\paperheight]{{{cover}}}}};"
    logo_node = ""
    if logo:
        logo_node = f"\\node[anchor=north east] at ([xshift=-12mm,yshift=-9mm]current page.north east) {{\\includegraphics[width=18mm]{{{logo}}}}};"
    return rf"""
\thispagestyle{{empty}}
\begin{{tikzpicture}}[remember picture,overlay]
{background}
\fill[white,opacity=.94] ([xshift=15mm,yshift=-18mm]current page.north west) rectangle ++(132mm,-70mm);
\fill[BOBright] ([xshift=15mm,yshift=-18mm]current page.north west) rectangle ++(132mm,-1.8mm);
{logo_node}
\node[anchor=north west,text width=120mm] at ([xshift=21mm,yshift=-25mm]current page.north west) {{\sffamily\scriptsize\bfseries\color{{BOBlue}} BLUEOCEAN\\DEEP RESEARCH REPORT}};
\node[anchor=north west,text width=120mm] at ([xshift=21mm,yshift=-39mm]current page.north west) {{\parbox{{120mm}}{{\raggedright\hyphenpenalty=10000\exhyphenpenalty=10000\sffamily\fontsize{{21}}{{23}}\selectfont\color{{BONavy}} {title}}}}};
\node[anchor=north west,text width=120mm] at ([xshift=21mm,yshift=-74mm]current page.north west) {{\sffamily\scriptsize\color{{BOMuted}} {subtitle}}};
\end{{tikzpicture}}
\clearpage
"""


def _summary_page(summary: List[str]) -> str:
    cards = []
    for idx, item in enumerate(summary[:6], start=1):
        cards.append(_summary_card(idx, item))
    while len(cards) < 6:
        cards.append(_summary_card(len(cards) + 1, "Evidence base and management implications should be validated through source backup and client discussion."))
    return rf"""
\kicker{{Key highlights}}
{{\Large\sffamily\bfseries\color{{BONavy}} The report opens with six decision-relevant conclusions}}\\[4pt]
\bohrule
\begin{{tabularx}}{{\linewidth}}{{Y Y}}
{cards[0]} & {cards[1]} \\\[7pt]
{cards[2]} & {cards[3]} \\\[7pt]
{cards[4]} & {cards[5]}
\end{{tabularx}}
\clearpage
"""


def _summary_card(idx: int, text: str) -> str:
    return rf"\colorbox{{BOLight}}{{\parbox{{0.455\linewidth}}{{\textcolor{{BOBlue}}{{\bfseries {idx:02d}}}\\[-1pt]{{\small {_tex(_shorten(text, 240))}}}}}}"


def _strategic_logic_page(report: Dict[str, Any], sections: List[Dict[str, Any]]) -> str:
    thesis = _tex(_shorten(report.get("report_subtitle") or "Strategy should focus on where evidence, policy, ecosystem maturity and commercialization converge.", 230))
    labels = [_strip_number_prefix(s.get("title", f"Pillar {idx}")) for idx, s in enumerate(sections[:4], start=1)]
    while len(labels) < 4:
        labels.append("Strategic pillar")
    labels = [_tex(_shorten(x, 58)) for x in labels]
    return rf"""
\kicker{{Strategic logic}}
{{\Large\sffamily\bfseries\color{{BONavy}} The assessment links market evidence to management action}}\\[4pt]
\bohrule
{{\small {thesis}}}\\[8pt]
\begin{{center}}
\begin{{tikzpicture}}[x=1mm,y=1mm]
\tikzstyle{{nodebox}}=[draw=BOLine,fill=BOLight,rounded corners=2mm,minimum width=39mm,minimum height=18mm,align=center,text width=35mm]
\node[nodebox] (a) at (22,48) {{\textcolor{{BOBlue}}{{\bfseries Evidence}}\\\scriptsize {labels[0]}}};
\node[nodebox] (b) at (70,48) {{\textcolor{{BOBlue}}{{\bfseries Policy}}\\\scriptsize {labels[1]}}};
\node[nodebox] (c) at (118,48) {{\textcolor{{BOBlue}}{{\bfseries Ecosystem}}\\\scriptsize {labels[2]}}};
\node[nodebox] (d) at (166,48) {{\textcolor{{BOBlue}}{{\bfseries Action}}\\\scriptsize {labels[3]}}};
\draw[->,very thick,BOBright] (a) -- (b);
\draw[->,very thick,BOBright] (b) -- (c);
\draw[->,very thick,BOBright] (c) -- (d);
\node[draw=BOBlue,fill=white,rounded corners=2mm,align=center,text width=145mm,minimum height=18mm] at (94,15) {{\small Client-ready recommendation requires a clear bridge from factual signal to investable / actionable choice.}};
\end{{tikzpicture}}
\end{{center}}
\begin{{tabularx}}{{\linewidth}}{{L{{34mm}}Y Y}}
\textcolor{{BOBlue}}{{\bfseries Lens}} & \textcolor{{BOBlue}}{{\bfseries What it tests}} & \textcolor{{BOBlue}}{{\bfseries Output in the report}} \\
Market / policy signal & Whether the opportunity is structurally supported or merely episodic & Conclusion-first chapter titles and a prioritized management agenda \\
Ecosystem maturity & Whether capability, partners and talent can turn technology into deployment & Native tables, matrices and issue maps embedded in the LaTeX report \\
Execution risk & Whether bottlenecks can change timing, valuation or partnership structure & Risk register and mitigation table in later chapters \\
\end{{tabularx}}
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
    lead = _tex(_shorten(section.get("lead", ""), 220))
    paragraphs = [_tex(p) for p in section.get("paragraphs", [])[:5]]
    takeaways = [_tex(_shorten(x, 130)) for x in section.get("key_takeaways", [])[:3]]
    image_path = _asset_path(assets.get(f"image-{idx}", ""))
    image_block = ""
    if image_path:
        image_block = f"\\begin{{center}}\\includegraphics[width=.78\\linewidth,height=46mm,keepaspectratio]{{{image_path}}}\\end{{center}}"

    if takeaways:
        takeaway_text = " ".join([f"\\textbullet\\ {x}" for x in takeaways])
    else:
        takeaway_text = "\\textbullet\\ Validate assumptions with the reference backup. \\textbullet\\ Translate evidence into management action."

    body = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs[:3]])
    continuation = "\n\n".join([f"{{\\small {p}}}" for p in paragraphs[3:]])
    tool = _analysis_tool(idx, title, paragraphs, takeaways)
    continuation_block = f"\n{{\\small {continuation}}}\n" if continuation else ""

    return rf"""
\clearpage
\kicker{{Chapter {idx}}}
{{\Large\sffamily\bfseries\color{{BONavy}} {title}}}\\[4pt]
{('\lead{' + lead + '}') if lead else ''}
{body}
\takeaway{{\textbf{{So what:}} {takeaway_text}}}
{image_block}
{tool}
{continuation_block}
"""


def _analysis_tool(idx: int, title: str, paragraphs: List[str], takeaways: List[str]) -> str:
    mode = ((idx - 1) % 6) + 1
    if mode == 1:
        return _architecture_diagram()
    if mode == 2:
        return _timeline_diagram()
    if mode == 3:
        return _ecosystem_table()
    if mode == 4:
        return _application_matrix()
    if mode == 5:
        return _risk_register()
    return _decision_table()


def _architecture_diagram() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Diagnostic architecture map}\\[-2pt]
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\tikzstyle{box}=[draw=BOLine,fill=BOLight,rounded corners=2mm,minimum width=38mm,minimum height=14mm,align=center,text width=35mm]
\node[box] (a) at (25,25) {\small Hardware / platform};
\node[box] (b) at (73,25) {\small Funding / policy};
\node[box] (c) at (121,25) {\small Ecosystem / talent};
\node[box] (d) at (169,25) {\small Commercial use cases};
\draw[->,very thick,BOBright] (a) -- (b);
\draw[->,very thick,BOBright] (b) -- (c);
\draw[->,very thick,BOBright] (c) -- (d);
\end{tikzpicture}
\end{center}
"""


def _timeline_diagram() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Indicative development roadmap}\\[-2pt]
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\draw[very thick,BOBright] (10,18) -- (170,18);
\foreach \x/\year/\label in {10/2021/Policy launch,55/2023/Pilot scaling,105/2026/Commercial proof,150/2030/Industrial adoption}{
  \fill[BOBlue] (\x,18) circle (2.1);
  \node[anchor=south,align=center,text width=32mm] at (\x,22) {\scriptsize\textcolor{BOBlue}{\textbf{\year}}\\\scriptsize \label};
}
\end{tikzpicture}
\end{center}
"""


def _ecosystem_table() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Ecosystem role map}\\[2pt]
\begin{tabularx}{\linewidth}{L{33mm}Y Y}
\textcolor{BOBlue}{\bfseries Actor} & \textcolor{BOBlue}{\bfseries Role in scaling} & \textcolor{BOBlue}{\bfseries Management implication} \\
Government / labs & Anchor funding, standards and strategic direction & Track policy shifts and non-market funding priorities \\
Large platforms & Provide cloud access, customer reach and infrastructure credibility & Use partnerships to accelerate go-to-market and validation \\
Startups & Specialize around algorithms, components and vertical applications & Prioritize teams with clear application roadmap and defensible talent \\
Customers & Convert pilots into repeatable deployment evidence & Focus on use cases with visible ROI and procurement urgency \\
\end{tabularx}
"""


def _application_matrix() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Application attractiveness screen}\\[2pt]
\begin{tabularx}{\linewidth}{L{36mm}L{27mm}L{27mm}Y}
\textcolor{BOBlue}{\bfseries Application} & \textcolor{BOBlue}{\bfseries Time to proof} & \textcolor{BOBlue}{\bfseries Value clarity} & \textcolor{BOBlue}{\bfseries Priority logic} \\
Optimization / logistics & Near term & Medium & Attractive where narrow advantage can be demonstrated with existing workflows \\
Finance / risk & Near term & High & Strong business sponsor, but deployment depends on reliability and governance \\
Drug / materials discovery & Medium term & High & Strategic upside is large, but technical proof and data integration remain demanding \\
Security / infrastructure & Medium term & Medium & Policy-driven demand can support early deployment even before broad commercial ROI \\
\end{tabularx}
"""


def _risk_register() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Risk register and mitigation logic}\\[2pt]
\begin{tabularx}{\linewidth}{L{36mm}L{25mm}Y}
\textcolor{BOBlue}{\bfseries Risk} & \textcolor{BOBlue}{\bfseries Severity} & \textcolor{BOBlue}{\bfseries Mitigation lens} \\
Technology bottleneck & High & Stage-gate investment against measurable performance milestones \\
Supply chain constraint & Medium-high & Build local alternatives, dual-source critical components and monitor export controls \\
Talent gap & Medium & Back teams with credible academic pipelines and retention model \\
Commercial adoption delay & Medium & Prioritize customers with urgent pain points and clear procurement sponsor \\
\end{tabularx}
"""


def _decision_table() -> str:
    return r"""
\vspace{4pt}
\boxtitle{Decision filter for management action}\\[2pt]
\begin{tabularx}{\linewidth}{L{34mm}Y Y}
\textcolor{BOBlue}{\bfseries Decision test} & \textcolor{BOBlue}{\bfseries What good looks like} & \textcolor{BOBlue}{\bfseries Warning sign} \\
Strategic fit & Reinforces a priority market, capability or policy-backed growth lane & Attractive technology without a buyer or owner \\
Evidence quality & Demonstrated performance data, reference customers and transparent assumptions & Claims depend on one-off demos or unsupported forecasts \\
Scalability & Repeatable economics, supply access and partner ecosystem & Deployment depends on bespoke lab conditions \\
Timing & Clear 12-36 month proof path & Value creation pushed beyond practical investment horizon \\
\end{tabularx}
"""


def _asset_path(path: str, *, allow_svg: bool = True) -> str:
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    if not allow_svg and normalized.lower().endswith(".svg"):
        return ""
    return normalized


def _summary_items(value: Any) -> List[str]:
    raw: List[str]
    if isinstance(value, list):
        raw = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = [str(value).strip()] if str(value).strip() else []
    if len(raw) <= 2 and raw and len(" ".join(raw)) > 450:
        text = " ".join(raw)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        raw = [s.strip() for s in sentences if len(s.strip()) > 20]
    return raw[:8]


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", str(text or "")).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."


def _tex(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00ad", "").replace("\ufffe", "").replace("\ufeff", "")
    text = " ".join(text.replace("\n", " ").split())
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
