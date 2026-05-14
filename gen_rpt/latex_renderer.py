from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

HEADER = r'''
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=13mm,top=12mm,bottom=13mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{tabularx}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{needspace}
\defaultfontfeatures{Ligatures=TeX}
\IfFontExistsTF{Noto Sans CJK SC}{\setmainfont{Noto Sans CJK SC}\setsansfont{Noto Sans CJK SC}}{\setmainfont{DejaVu Sans}\setsansfont{DejaVu Sans}}
\definecolor{BOBlue}{HTML}{0055A4}
\definecolor{BOBright}{HTML}{3273F6}
\definecolor{BONavy}{HTML}{051C2C}
\definecolor{BOMuted}{HTML}{6F7F8F}
\definecolor{BOLine}{HTML}{DCE3EA}
\definecolor{BOLight}{HTML}{F4F8FC}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.4pt}
\setlength{\tabcolsep}{5pt}
\renewcommand{\arraystretch}{1.16}
\hyphenpenalty=9000
\exhyphenpenalty=9000
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
    parts.append(_portfolio_view(sections, assets))
    for idx, section in enumerate(sections, start=1):
        parts.append(_chapter_block(section, assets, idx))
    parts.append(_disclaimer_page(refs))
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, subtitle: str, cover: str) -> str:
    bg = ''
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[BONavy,opacity=.20] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    return r'''
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\fill[white,opacity=.96] ([xshift=17mm,yshift=-25mm]current page.north west) rectangle ++(148mm,-72mm);
\fill[BOBright] ([xshift=17mm,yshift=-25mm]current page.north west) rectangle ++(148mm,-2.1mm);
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-34mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOBlue} BLUEOCEAN\\DEEP RESEARCH REPORT};
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-49mm]current page.north west) {\parbox{132mm}{\raggedright\sffamily\fontsize{22}{25}\selectfont\color{BONavy} ''' + title + r'''}};
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-86mm]current page.north west) {\sffamily\small\color{BOMuted} ''' + subtitle + r'''};
\end{tikzpicture}
\clearpage
'''


def _summary_page(summary: List[str]) -> str:
    rows = []
    for idx, item in enumerate(summary[:8], start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\small ' + _tex(_shorten(item, 330)) + '} \\\\[5pt]\n')
    if not rows:
        rows.append('\\textcolor{BOBlue}{\\bfseries 01} & {\\small Leadership should align resources to the few facts that change strategic choices.} \\\\[5pt]\n')
    return _kicker('Executive conclusions') + _heading('Management should focus on the few moves that can change the outcome') + _rule() + '\\begin{tabularx}{\\linewidth}{p{13mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\vspace{5pt}\n' + _summary_bars() + '\\clearpage\n'


def _contents_page(sections: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, section in enumerate(sections, start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + str(idx) + '} & ' + _tex(_strip_number_prefix(section.get('title', 'Section'))) + ' \\\\[4pt]\n')
    return _kicker('Contents') + _heading('Contents') + _rule() + '\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\clearpage\n'


def _portfolio_view(sections: List[Dict[str, Any]], assets: Dict[str, str]) -> str:
    labels = [_tex(_shorten(_strip_number_prefix(s.get('title', 'Chapter')), 36)) for s in sections[:6]]
    while len(labels) < 6:
        labels.append('Strategic theme')
    return _kicker('Where to focus') + _heading('The core question is where facts, economics and execution timing intersect') + _rule() + r'''
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\draw[BOLine] (0,0) rectangle (170,72);
\draw[BOLine] (85,0) -- (85,72); \draw[BOLine] (0,36) -- (170,36);
\node[anchor=west] at (3,68) {\scriptsize Higher strategic materiality};
\node[anchor=west] at (3,4) {\scriptsize Lower strategic materiality};
\node[anchor=south] at (30,74) {\scriptsize Harder to execute};
\node[anchor=south] at (122,74) {\scriptsize Easier to execute};
'''+ _bubble(120, 54, labels[0], 'BOBright') + _bubble(96, 44, labels[1], 'BOBlue') + _bubble(62, 52, labels[2], 'BOMuted') + _bubble(128, 27, labels[3], 'BOBlue') + _bubble(50, 22, labels[4], 'BOMuted') + _bubble(150, 15, labels[5], 'BOBright') + r'''
\end{tikzpicture}
\end{center}
\vspace{5pt}
\begin{tabularx}{\linewidth}{p{43mm}Y}
\textcolor{BOBlue}{\bfseries What this means} & The report should be read as a sequence of choices: first, identify where the topic is strategically material; second, test whether the economics and operating constraints are real; third, define the actions that leadership can take before the market fully resolves. \\
\textcolor{BOBlue}{\bfseries How to use it} & Management should pressure-test each conclusion against source evidence, milestone timing, partner availability and the organisation's ability to act faster than competitors. \\
\end{tabularx}
\clearpage
'''


def _chapter_block(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    lead = _tex(_shorten(section.get('lead', ''), 270))
    paras = _paras(section)
    visual = _image_block(_resolve_image(section, assets, idx), '66mm', '46mm')
    chart = _image_block(_resolve_chart(assets, idx), '155mm', '70mm')
    takeaways = [_tex(_shorten(x, 170)) for x in section.get('key_takeaways', [])[:4]]
    if not takeaways:
        takeaways = ['Translate the evidence into a specific management choice.', 'Prioritize segments where urgency and feasibility overlap.', 'Track milestones that can change the investment case.']
    take_rows = ''.join(['\\textcolor{BOBlue}{\\bfseries ' + str(i + 1) + '} & ' + takeaways[i] + ' \\\\[4pt]\n' for i in range(len(takeaways))])
    chapter = '\\Needspace{90mm}\n' + _kicker('Chapter ' + str(idx)) + _heading(title)
    if lead:
        chapter += '{\\textcolor{BOBlue}{\\normalsize ' + lead + '}}\\par\\vspace{3pt}\n'
    chapter += '\\begin{minipage}[t]{0.57\\linewidth}\n' + _para(paras[0]) + _para(paras[1]) + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.38\\linewidth}\n\\vspace{0pt}\n' + visual + '\n\\end{minipage}\\par\\vspace{4pt}\n'
    chapter += _para(paras[2]) + _para(paras[3])
    chapter += _subhead('Evidence') + chart + '\\vspace{3pt}\n' + _para(paras[4])
    chapter += _clean_exhibit_note(idx)
    chapter += _subhead('Management priorities') + '\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n' + take_rows + '\\end{tabularx}\n'
    chapter += _choice_table(idx)
    chapter += '\\vspace{8pt}\n'
    return chapter


def _disclaimer_page(refs: List[Any]) -> str:
    note = ''
    if refs:
        note = '\\vspace{6pt}{\\small\\color{BOMuted} This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. The detailed source backup is retained in the backup folder rather than reproduced in the client-facing document.}\\par'
    return '\\clearpage\n' + _kicker('Disclaimer') + _heading('This report is a management consulting analysis, not investment advice') + _rule() + '{\\small This document has been prepared by BlueOcean for strategy discussion, industry analysis and executive decision support. It is not intended to constitute investment advice, securities research, legal advice, tax advice, audit assurance, or a recommendation to buy or sell any security or financial instrument. The analysis relies on public sources, model-assisted synthesis and management-consulting judgment. Market estimates, forecasts and scenarios are directional and should be independently validated before they are used for investment, financing, transaction, regulatory or operational decisions.}\\par\n' + note + '\\vspace{6pt}{\\small\\color{BOMuted} BlueOcean does not guarantee the completeness or accuracy of third-party information and accepts no responsibility for decisions made solely on the basis of this document.}\n'


def _choice_table(idx: int) -> str:
    rows = [
        ('Where to compete', 'Prioritize the arenas where the evidence supports both value creation and right-to-win.'),
        ('How to participate', 'Choose the lightest credible commitment first, then scale when milestones reduce uncertainty.'),
        ('What to monitor', 'Track the two or three indicators most likely to change timing, economics or partner access.'),
    ]
    body = ''.join([_tex(a) + ' & ' + _tex(b) + ' \\\\ \n' for a, b in rows])
    return _subhead('Leadership choices') + '\\begin{tabularx}{\\linewidth}{p{42mm}Y}\n' + body + '\\end{tabularx}\n'


def _clean_exhibit_note(idx: int) -> str:
    return '{\\scriptsize\\color{BOMuted} Exhibit ' + str(idx) + ' is directional and should be validated against the source backup before being used for capital allocation or transaction decisions.}\\par\\vspace{3pt}\n'


def _summary_bars() -> str:
    return r'''
\begin{center}
\begin{tikzpicture}[x=1mm,y=1mm]
\fill[BOLight] (0,0) rectangle +(38,8); \fill[BOBright] (0,0) rectangle +(28,8); \node[anchor=west] at (0,11) {\scriptsize Evidence};
\fill[BOLight] (46,0) rectangle +(38,8); \fill[BOBright] (46,0) rectangle +(24,8); \node[anchor=west] at (46,11) {\scriptsize Economics};
\fill[BOLight] (92,0) rectangle +(38,8); \fill[BOBright] (92,0) rectangle +(21,8); \node[anchor=west] at (92,11) {\scriptsize Timing};
\fill[BOLight] (138,0) rectangle +(38,8); \fill[BOBright] (138,0) rectangle +(18,8); \node[anchor=west] at (138,11) {\scriptsize Execution};
\end{tikzpicture}
\end{center}
'''


def _bubble(x: int, y: int, label: str, color: str) -> str:
    return '\\fill[' + color + '] (' + str(x) + ',' + str(y) + ') circle (2.8); \\node[anchor=west,text width=42mm] at (' + str(x + 4) + ',' + str(y) + ') {\\scriptsize ' + label + '};\n'


def _paras(section: Dict[str, Any]) -> List[str]:
    raw = [str(x) for x in section.get('paragraphs', []) if str(x).strip()]
    lead = str(section.get('lead', '') or '').strip()
    if lead:
        raw.insert(0, lead)
    expansions = [
        'The important management question is not whether the signal exists, but whether it is strong enough to justify a change in priorities, partnerships or capital allocation.',
        'A practical reading of the evidence should distinguish between near-term operating choices and longer-term strategic options that remain conditional on future milestones.',
        'The strongest response is to define a small number of measurable proof points, assign owners, and revisit the decision as the evidence base improves.',
    ]
    while len(raw) < 6:
        raw.append(expansions[(len(raw) - 1) % len(expansions)])
    return [_tex(x) for x in raw[:6]]


def _para(text: str) -> str:
    return '{\\small ' + text + '}\\par\n'


def _image_block(path: str, width: str, height: str) -> str:
    if path:
        return '\\includegraphics[width=' + width + ',height=' + height + ',keepaspectratio]{' + path + '}'
    return '\\fbox{\\parbox[c][' + height + '][c]{' + width + '}{\\centering\\scriptsize\\color{BOMuted} Visual to be generated}}'


def _resolve_image(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    for key in [f'image-{idx}', str(section.get('visual_hint', ''))]:
        path = _asset_path(assets.get(key, ''))
        if path:
            return path
    return _asset_path(assets.get('cover-background', ''))


def _resolve_chart(assets: Dict[str, str], idx: int) -> str:
    for key in [f'chart-{idx}', f'chart-{((idx - 1) % 6) + 1}']:
        path = _asset_path(assets.get(key, ''))
        if path:
            return path
    return ''


def _kicker(text: str) -> str:
    return '{\\textcolor{BOBlue}{\\scriptsize\\bfseries ' + _tex(str(text).upper()) + '}}\\par\\vspace{-1pt}\n'


def _heading(text: str) -> str:
    return '{\\Large\\sffamily\\bfseries\\color{BONavy} ' + text + '}\\par\\vspace{4pt}\n'


def _subhead(text: str) -> str:
    return '\\vspace{5pt}{\\textcolor{BOBlue}{\\scriptsize\\bfseries ' + _tex(text.upper()) + '}}\\par\n'


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
