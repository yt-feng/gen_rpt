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
\setlength{\parskip}{3.1pt}
\setlength{\tabcolsep}{5pt}
\renewcommand{\arraystretch}{1.13}
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
    sections = _safe_sections(report.get('sections', []))
    summary = _summary_items(report.get('executive_summary', []))
    refs = report.get('reference_institutions', []) or []
    parts = [HEADER, '\\begin{document}', '\\raggedright']
    parts.append(_cover_page(title, _asset_path(assets.get('cover-background', ''))))
    parts.append(_agenda_and_contents_page(summary, sections))
    for idx, section in enumerate(sections, start=1):
        parts.append(_chapter_block(section, assets, idx))
    parts.append(_disclaimer_page(refs))
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, cover: str) -> str:
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[BONavy,opacity=.18] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    return r'''
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\fill[white,opacity=.96] ([xshift=17mm,yshift=-28mm]current page.north west) rectangle ++(150mm,-62mm);
\fill[BOBright] ([xshift=17mm,yshift=-28mm]current page.north west) rectangle ++(150mm,-2.1mm);
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-37mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOBlue} BLUEOCEAN\\DEEP RESEARCH REPORT};
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-53mm]current page.north west) {\parbox{132mm}{\raggedright\sffamily\fontsize{22}{25}\selectfont\color{BONavy} ''' + title + r'''}};
\end{tikzpicture}
\clearpage
'''


def _agenda_and_contents_page(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    heading = _agenda_heading(summary, sections)
    summary_rows = []
    for idx, item in enumerate(summary[:5], start=1):
        summary_rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\footnotesize ' + _tex(_shorten(item, 250)) + '} \\\\[4pt]\n')
    if not summary_rows:
        summary_rows.append('\\textcolor{BOBlue}{\\bfseries 01} & {\\footnotesize Leadership should align resources to the few facts that change strategic choices.} \\\\[4pt]\n')

    content_rows = []
    for idx, section in enumerate(sections, start=1):
        title = _tex(_strip_number_prefix(section.get('title', 'Section')))
        content_rows.append('\\textcolor{BOBlue}{\\bfseries ' + str(idx) + '} & ' + title + ' & {\\color{BOMuted}p.~\\pageref{chap:' + str(idx) + '}} \\\\[3pt]\n')

    return _kicker('Executive conclusions and contents') + _heading(heading) + _rule() + '\\begin{tabularx}{\\linewidth}{p{12mm}Y}\n' + ''.join(summary_rows) + '\\end{tabularx}\n\\vspace{6pt}\n{\\textcolor{BOBlue}{\\scriptsize\\bfseries CONTENTS}}\\par\\vspace{2pt}\n\\begin{tabularx}{\\linewidth}{p{8mm}Yp{18mm}}\n' + ''.join(content_rows) + '\\end{tabularx}\n\\clearpage\n'


def _chapter_block(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    lead = _tex(_shorten(section.get('lead', ''), 270))
    paras = _paras(section)
    visual = _image_block(_resolve_image(section, assets, idx), '66mm', '46mm')
    chart_path = _resolve_chart(assets, idx)
    chart = _image_block(chart_path, '150mm', '64mm') if chart_path else ''
    chapter = '\\Needspace{34mm}\n\\label{chap:' + str(idx) + '}\n' + _kicker('Chapter ' + str(idx)) + _heading(title)
    if lead:
        chapter += '{\\textcolor{BOBlue}{\\normalsize ' + lead + '}}\\par\\vspace{3pt}\n'
    chapter += '\\begin{minipage}[t]{0.57\\linewidth}\n' + _para(paras[0]) + _para(paras[1]) + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.38\\linewidth}\n\\vspace{0pt}\n' + visual + '\n\\end{minipage}\\par\\vspace{4pt}\n'
    chapter += _para(paras[2]) + _para(paras[3])
    if chart:
        chapter += '\\vspace{4pt}\n' + chart + '\\vspace{3pt}\n'
    chapter += _para(paras[4]) + _para(paras[5])
    chapter += '\\vspace{8pt}\n'
    return chapter


def _disclaimer_page(refs: List[Any]) -> str:
    reference_note = ''
    if refs:
        reference_note = 'This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. The detailed source backup is retained in the backup folder rather than reproduced in the client-facing document. '
    body = (
        'This document has been prepared by BlueOcean for strategy discussion, industry analysis and executive decision support. It is not intended to constitute investment advice, securities research, legal advice, tax advice, audit assurance, fairness opinion, valuation opinion, or a recommendation to buy or sell any security, financial instrument, company, project or asset. '
        'The analysis relies on public sources, model-assisted synthesis and management-consulting judgment. Market estimates, forecasts and scenarios are directional and should be independently validated before they are used for investment, financing, transaction, regulatory or operational decisions. '
        'Any forward-looking views are inherently uncertain and may change as technology, policy, financing, regulation, competition, supply chains and macro conditions evolve. BlueOcean does not guarantee the completeness, accuracy or timeliness of third-party information and accepts no responsibility for decisions made solely on the basis of this document. '
        + reference_note +
        'Recipients should perform their own diligence, consult professional advisers where appropriate, and treat this report as one input into a broader decision process rather than as a definitive factual record.'
    )
    filler = body + ' ' + body
    return '\\clearpage\n{\\textcolor{BOBlue}{\\scriptsize\\bfseries DISCLAIMER}}\\par\\vspace{3pt}\n{\\Large\\sffamily\\bfseries\\color{BONavy} Disclaimer}\\par\\vspace{4pt}\n' + _rule() + '{\\footnotesize\\color{BOMuted} ' + filler + '}\n'


def _agenda_heading(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    for item in summary:
        cleaned = _normalize_punctuation(str(item or '').strip())
        if not cleaned:
            continue
        if cleaned.lower().startswith(('this report', 'the report', 'our analysis')):
            continue
        return _tex(_shorten(cleaned, 118))
    if sections:
        return _tex(_shorten(_strip_number_prefix(sections[0].get('title', 'Management agenda')), 118))
    return 'Management should focus on the few moves that can change the outcome'


def _paras(section: Dict[str, Any]) -> List[str]:
    raw = [str(x) for x in section.get('paragraphs', []) if str(x).strip()]
    lead = str(section.get('lead', '') or '').strip()
    if lead:
        raw.insert(0, lead)
    expansions = [
        'For executives, the practical implication is to distinguish facts that should change resource allocation from signals that are still too early to underwrite a major commitment.',
        'The strongest near-term stance is to define measurable proof points, maintain flexibility, and revisit the opportunity as technical, policy and customer milestones become clearer.',
        'The management agenda should therefore sequence actions by materiality, feasibility and timing rather than treating the market as a single binary bet.',
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
    return [_normalize_punctuation(x) for x in raw[:8]]


def _strip_number_prefix(text: str) -> str:
    return re.sub(r'^\s*\d+[\.)、]\s*', '', str(text or '')).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = ' '.join(str(value or '').replace('\n', ' ').split())
    text = _normalize_punctuation(text)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + '.'


def _normalize_punctuation(text: str) -> str:
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"', '\u2032': "'", '\u2033': '"',
        '\uff02': '"', '\uff07': "'", '\uff0c': ',', '\uff0e': '.', '\uff1a': ':', '\uff1b': ';',
        '\uff08': '(', '\uff09': ')', '\u2013': '-', '\u2014': '-', '\u00a0': ' ',
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _tex(value: Any) -> str:
    text = str(value or '').replace('\u00ad', '').replace('\ufffe', '').replace('\ufeff', '')
    text = _normalize_punctuation(' '.join(text.replace('\n', ' ').split()))
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)
