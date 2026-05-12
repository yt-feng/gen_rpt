from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

HEADER = r'''
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=13mm,top=11mm,bottom=12mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{tabularx}
\usepackage{array}
\usepackage{fancyhdr}
\defaultfontfeatures{Ligatures=TeX}
\IfFontExistsTF{Noto Sans CJK SC}{\setmainfont{Noto Sans CJK SC}\setsansfont{Noto Sans CJK SC}}{\setmainfont{DejaVu Sans}\setsansfont{DejaVu Sans}}
\definecolor{BOBlue}{HTML}{0055A4}
\definecolor{BOBright}{HTML}{3273F6}
\definecolor{BONavy}{HTML}{051C2C}
\definecolor{BOMuted}{HTML}{6F7F8F}
\definecolor{BOLine}{HTML}{DCE3EA}
\definecolor{BOLight}{HTML}{F4F8FC}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.8pt}
\setlength{\tabcolsep}{5pt}
\renewcommand{\arraystretch}{1.20}
\hyphenpenalty=10000
\exhyphenpenalty=10000
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
'''


def render_latex_pdf(report: Dict[str, Any], assets: Dict[str, str], output_dir: Path, topic: str, language: str = 'en') -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path = output_dir / 'report_latex.tex'
    pdf_path = output_dir / 'report_latex.pdf'
    tex_path.write_text(_build_tex(report, assets, topic), encoding='utf-8')
    xelatex = shutil.which('xelatex')
    if not xelatex:
        (output_dir / 'latex_error.txt').write_text('xelatex not found.\n', encoding='utf-8')
        return {'tex_path': str(tex_path), 'pdf_path': ''}
    try:
        log = ''
        for _ in range(2):
            run = subprocess.run([xelatex, '-interaction=nonstopmode', '-halt-on-error', tex_path.name], cwd=str(output_dir), check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            log += run.stdout[-4000:]
        (output_dir / 'latex_build.log').write_text(log, encoding='utf-8')
        return {'tex_path': str(tex_path), 'pdf_path': str(pdf_path) if pdf_path.exists() else ''}
    except subprocess.CalledProcessError as exc:
        (output_dir / 'latex_error.txt').write_text((exc.stdout or str(exc))[-8000:], encoding='utf-8')
        return {'tex_path': str(tex_path), 'pdf_path': ''}
    except Exception as exc:
        (output_dir / 'latex_error.txt').write_text(str(exc), encoding='utf-8')
        return {'tex_path': str(tex_path), 'pdf_path': ''}


def _build_tex(report: Dict[str, Any], assets: Dict[str, str], topic: str) -> str:
    title = _tex(report.get('report_title') or topic)
    subtitle = _tex(report.get('report_subtitle') or 'Strategic assessment')
    sections = _safe_sections(report.get('sections', []))
    summary = _summary_items(report.get('executive_summary', []))
    refs = report.get('reference_institutions', []) or []
    parts = [HEADER, '\\begin{document}', '\\raggedright']
    parts.append(_cover_page(title, subtitle, _asset_path(assets.get('cover-background', ''))))
    parts.append(_summary_page(summary))
    parts.append(_contents_page(sections))
    for idx, section in enumerate(sections, start=1):
        parts.append(_section_opener(section, assets, idx))
        parts.append(_section_evidence(section, assets, idx))
        parts.append(_section_implications(section, assets, idx))
    parts.append(_disclaimer_page(refs))
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, subtitle: str, cover: str) -> str:
    bg = ''
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[white,opacity=.18] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    return r'''
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\fill[white,opacity=.96] ([xshift=16mm,yshift=-20mm]current page.north west) rectangle ++(140mm,-72mm);
\fill[BOBright] ([xshift=16mm,yshift=-20mm]current page.north west) rectangle ++(140mm,-2mm);
\node[anchor=north west,text width=126mm] at ([xshift=23mm,yshift=-29mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOBlue} BLUEOCEAN\\DEEP RESEARCH REPORT};
\node[anchor=north west,text width=126mm] at ([xshift=23mm,yshift=-43mm]current page.north west) {\parbox{126mm}{\raggedright\sffamily\fontsize{22}{25}\selectfont\color{BONavy} ''' + title + r'''}};
\node[anchor=north west,text width=126mm] at ([xshift=23mm,yshift=-80mm]current page.north west) {\sffamily\small\color{BOMuted} ''' + subtitle + r'''};
\end{tikzpicture}
\clearpage
'''


def _summary_page(summary: List[str]) -> str:
    rows = []
    for idx, item in enumerate(summary[:8], start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\small ' + _tex(_shorten(item, 330)) + '} \\\\[6pt]\n')
    if not rows:
        rows.append('\\textcolor{BOBlue}{\\bfseries 01} & {\\small The report prioritizes the facts that change management decisions.} \\\\[6pt]\n')
    return _kicker('Executive conclusions') + _heading('The market is shifting fast enough to require a clear management agenda') + _rule() + '\\begin{tabularx}{\\linewidth}{p{13mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\clearpage\n'


def _contents_page(sections: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, section in enumerate(sections, start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + str(idx) + '} & ' + _tex(_strip_number_prefix(section.get('title', 'Section'))) + ' \\\\[5pt]\n')
    return _kicker('Contents') + _heading('Contents') + _rule() + '\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\clearpage\n'


def _section_opener(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    lead = _tex(_shorten(section.get('lead', ''), 260))
    paras = _paras(section)
    image = _image_block(_resolve_image(section, assets, idx), '74mm', '54mm')
    return '\\clearpage\n' + _kicker('Chapter ' + str(idx)) + _heading(title) + ('{\\textcolor{BOBlue}{\\large ' + lead + '}}\\par\\vspace{4pt}\n' if lead else '') + '\\begin{minipage}[t]{0.52\\linewidth}\n' + _para(paras[0]) + _para(paras[1]) + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.42\\linewidth}\n' + image + '\n\\end{minipage}\n\\vspace{5pt}\n' + _para(paras[2]) + _section_table(idx)


def _section_evidence(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    chart = _image_block(_resolve_chart(assets, idx), '170mm', '88mm')
    paras = _paras(section)
    return '\\clearpage\n' + _kicker('Evidence exhibit') + _heading(title) + chart + '\\vspace{5pt}\n' + _para(paras[3]) + _para(_evidence_sentence(title)) + _matrix_block(idx)


def _section_implications(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    paras = _paras(section)
    takes = [_tex(_shorten(x, 170)) for x in section.get('key_takeaways', [])[:4]]
    if not takes:
        takes = [_tex('Prioritize management actions that change resource allocation.'), _tex('Validate the evidence base before committing capital.'), _tex('Sequence initiatives by materiality, feasibility and timing.')]
    rows = ''.join(['\\textcolor{BOBlue}{\\bfseries ' + str(i + 1) + '} & ' + takes[i] + ' \\\\[5pt]\n' for i in range(len(takes))])
    return '\\clearpage\n' + _kicker('Management implications') + _heading(title) + _para(paras[4]) + '\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n' + rows + '\\end{tabularx}\n\\vspace{6pt}\n' + _roadmap_block(idx)


def _disclaimer_page(refs: List[Any]) -> str:
    note = ''
    if refs:
        note = '\\vspace{6pt}{\\small\\color{BOMuted} This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. The detailed source backup is retained in the backup folder rather than reproduced in the client-facing document.}\\par'
    return '\\clearpage\n' + _kicker('Disclaimer') + _heading('This report is a management consulting analysis, not investment advice') + _rule() + '{\\small This document has been prepared by BlueOcean for strategy discussion, industry analysis and executive decision support. It is not intended to constitute investment advice, securities research, legal advice, tax advice, audit assurance, or a recommendation to buy or sell any security or financial instrument. The analysis relies on public sources, model-assisted synthesis and management-consulting judgment. Market estimates, forecasts and scenarios are directional and should be independently validated before they are used for investment, financing, transaction, regulatory or operational decisions.}\\par\n' + note + '\\vspace{6pt}{\\small\\color{BOMuted} BlueOcean does not guarantee the completeness or accuracy of third-party information and accepts no responsibility for decisions made solely on the basis of this document.}\n'


def _section_table(idx: int) -> str:
    return r'''
\vspace{6pt}{\textcolor{BOBlue}{\scriptsize\bfseries DECISION LENS}}\par
\begin{tabularx}{\linewidth}{p{42mm}Y}
\textcolor{BOBlue}{\bfseries Question} & \textcolor{BOBlue}{\bfseries Why it matters} \\
Market direction & Separates structural change from temporary noise \\
Competitive response & Identifies where incumbents, challengers and customers may move next \\
Capital priority & Connects the argument to resource allocation and timing \\
\end{tabularx}
'''


def _matrix_block(idx: int) -> str:
    return r'''
\vspace{6pt}{\textcolor{BOBlue}{\scriptsize\bfseries STRATEGIC POSITIONING MAP}}\par
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\draw[BOLine] (0,0) rectangle (150,56);
\draw[BOLine] (75,0) -- (75,56); \draw[BOLine] (0,28) -- (150,28);
\node[anchor=west] at (3,52) {\scriptsize High urgency};
\node[anchor=west] at (3,3) {\scriptsize Lower urgency};
\node[anchor=south] at (20,57) {\scriptsize Lower readiness};
\node[anchor=south] at (112,57) {\scriptsize Higher readiness};
\fill[BOBright] (108,42) circle (3.2); \node[anchor=west] at (113,42) {\scriptsize Priority arena};
\fill[BOBlue] (55,36) circle (2.6); \node[anchor=west] at (60,36) {\scriptsize Monitor};
\fill[BOMuted] (95,16) circle (2.4); \node[anchor=west] at (100,16) {\scriptsize Sequence later};
\end{tikzpicture}
\end{center}
'''


def _roadmap_block(idx: int) -> str:
    return r'''
\vspace{6pt}{\textcolor{BOBlue}{\scriptsize\bfseries EXECUTION ROADMAP}}\par
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\draw[very thick,BOBright] (10,18) -- (166,18);
\fill[BOBlue] (10,18) circle (2.2); \node[anchor=south,align=center,text width=34mm] at (10,22) {\scriptsize\textcolor{BOBlue}{\textbf{Now}}\\Confirm evidence};
\fill[BOBlue] (62,18) circle (2.2); \node[anchor=south,align=center,text width=34mm] at (62,22) {\scriptsize\textcolor{BOBlue}{\textbf{Next}}\\Prioritize moves};
\fill[BOBlue] (114,18) circle (2.2); \node[anchor=south,align=center,text width=34mm] at (114,22) {\scriptsize\textcolor{BOBlue}{\textbf{Then}}\\Commit resources};
\fill[BOBlue] (166,18) circle (2.2); \node[anchor=south,align=center,text width=34mm] at (166,22) {\scriptsize\textcolor{BOBlue}{\textbf{Scale}}\\Institutionalize};
\end{tikzpicture}
\end{center}
'''


def _paras(section: Dict[str, Any]) -> List[str]:
    raw = [str(x) for x in section.get('paragraphs', []) if str(x).strip()]
    lead = str(section.get('lead', '') or '').strip()
    if lead:
        raw.insert(0, lead)
    while len(raw) < 5:
        raw.append('The implication for executives is to translate the signal into a sequenced agenda: validate the facts, prioritize where the economics are strongest, and assign accountable owners for the next wave of decisions.')
    return [_tex(x) for x in raw[:5]]


def _para(text: str) -> str:
    return '{\\small ' + text + '}\\par\n'


def _image_block(path: str, width: str, height: str) -> str:
    if path:
        return '\\includegraphics[width=' + width + ',height=' + height + ',keepaspectratio]{' + path + '}'
    return '\\fbox{\\parbox[c][' + height + '][c]{' + width + '}{\\centering\\scriptsize\\color{BOMuted} Visual to be generated}}'


def _resolve_image(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    for key in [f'image-{idx}', str(section.get('visual_hint', '')), 'cover-background']:
        path = _asset_path(assets.get(key, ''))
        if path:
            return path
    return ''


def _resolve_chart(assets: Dict[str, str], idx: int) -> str:
    for key in [f'chart-{idx}', f'chart-{((idx - 1) % 6) + 1}']:
        path = _asset_path(assets.get(key, ''))
        if path:
            return path
    return ''


def _evidence_sentence(title: str) -> str:
    return 'The exhibit should be read as directional evidence: it frames the relative magnitude, timing and management relevance of the issue rather than replacing diligence on the underlying source data.'


def _kicker(text: str) -> str:
    return '{\\textcolor{BOBlue}{\\scriptsize\\bfseries ' + _tex(str(text).upper()) + '}}\\par\\vspace{-1pt}\n'


def _heading(text: str) -> str:
    return '{\\Large\\sffamily\\bfseries\\color{BONavy} ' + text + '}\\par\\vspace{4pt}\n'


def _rule() -> str:
    return '\\vspace{2pt}{\\color{BOBright}\\rule{\\linewidth}{1pt}}\\vspace{5pt}\n'


def _asset_path(path: str) -> str:
    if not path:
        return ''
    normalized = path.replace('\\', '/')
    return '' if normalized.lower().endswith('.svg') else normalized


def _safe_sections(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list) and value:
        return [x if isinstance(x, dict) else {'title': str(x), 'paragraphs': [str(x)]} for x in value]
    return [{'title': 'Executive priorities and implications', 'lead': 'The analysis should be translated into a concise management agenda.', 'paragraphs': ['The evidence should be organized around decision quality, execution timing and management implications.'], 'key_takeaways': ['Prioritize actionability.'], 'visual_hint': 'image-1'}]


def _summary_items(value: Any) -> List[str]:
    raw = [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else ([str(value).strip()] if str(value).strip() else [])
    if len(raw) <= 2 and raw and len(' '.join(raw)) > 450:
        raw = [s.strip() for s in re.split(r'(?<=[.!?])\s+', ' '.join(raw)) if len(s.strip()) > 20]
    return raw[:8]


def _strip_number_prefix(text: str) -> str:
    return re.sub(r'^\s*\d+[\.)、]\s*', '', str(text or '')).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = ' '.join(str(value or '').replace('\n', ' ').split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + '.'


def _tex(value: Any) -> str:
    text = str(value or '').replace('\u00ad', '').replace('\ufffe', '').replace('\ufeff', '')
    text = ' '.join(text.replace('\n', ' ').split())
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)
