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
'''


def render_latex_pdf(report: Dict[str, Any], assets: Dict[str, str], output_dir: Path, topic: str, language: str = 'en') -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path = output_dir / 'report_latex.tex'
    pdf_path = output_dir / 'report_latex.pdf'
    tex_path.write_text(_build_tex(report, assets, topic), encoding='utf-8')
    xelatex = shutil.which('xelatex')
    if not xelatex:
        (output_dir / 'latex_error.txt').write_text('xelatex not found; report_latex.tex was generated but not compiled.\n', encoding='utf-8')
        return {'tex_path': str(tex_path), 'pdf_path': ''}
    try:
        combined = ''
        for _ in range(2):
            run = subprocess.run([xelatex, '-interaction=nonstopmode', '-halt-on-error', tex_path.name], cwd=str(output_dir), check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            combined += run.stdout[-4000:]
        (output_dir / 'latex_build.log').write_text(combined, encoding='utf-8')
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
    parts.append(_disclaimer_page(refs))
    for idx, section in enumerate(sections, start=1):
        parts.append(_section_page(section, assets, idx))
    if refs:
        parts.append('\\clearpage\n' + _kicker('Reference note') + '{\\small\\color{BOMuted} This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. Full source backup is archived in the backup folder.}\n')
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, subtitle: str, cover: str) -> str:
    bg = ''
    if cover:
        bg = '\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}\\par\n'
    return '\\thispagestyle{empty}\n' + bg + '\\vspace*{10mm}\n{\\textcolor{BOBlue}{\\scriptsize\\bfseries BLUEOCEAN\\\\DEEP RESEARCH REPORT}}\\par\\vspace{8mm}\n{\\fontsize{24}{27}\\selectfont\\color{BONavy} ' + title + '}\\par\\vspace{6mm}\n{\\small\\color{BOMuted} ' + subtitle + '}\\clearpage\n'


def _summary_page(summary: List[str]) -> str:
    rows = []
    for idx, item in enumerate(summary[:8], start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\small ' + _tex(_shorten(item, 300)) + '} \\\\[5pt]\n')
    if not rows:
        rows.append('\\textcolor{BOBlue}{\\bfseries 01} & {\\small Evidence base and management implications should be validated.} \\\\[5pt]\n')
    return _kicker('Key highlights') + _heading('The report opens with decision-relevant conclusions') + _rule() + '\\begin{tabularx}{\\linewidth}{p{12mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\clearpage\n'


def _contents_page(sections: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, section in enumerate(sections, start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + str(idx) + '} & ' + _tex(_strip_number_prefix(section.get('title', 'Section'))) + ' \\\\[4pt]\n')
    return _kicker('Contents') + _heading('Contents') + _rule() + '\\begin{tabularx}{\\linewidth}{p{10mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n\\clearpage\n'


def _disclaimer_page(refs: List[Any]) -> str:
    note = ''
    if refs:
        note = '\\vspace{6pt}{\\scriptsize\\color{BOMuted} This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '.}'
    return _kicker('Disclaimer') + _heading('Disclaimer') + _rule() + '{\\small This document is for management research and strategy discussion only.}\\par\n' + note + '\\vspace{6pt}\n{\\scriptsize\\color{BOMuted} Full source backup is archived in the backup folder.}\\clearpage\n'


def _section_page(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_strip_number_prefix(section.get('title', f'Section {idx}')))
    lead = _tex(_shorten(section.get('lead', ''), 230))
    paragraphs = [_tex(x) for x in section.get('paragraphs', [])[:5]]
    while len(paragraphs) < 3:
        paragraphs.append('Evidence should be translated into management implications and validated against the source backup.')
    takes = [_tex(_shorten(x, 135)) for x in section.get('key_takeaways', [])[:3]]
    take_text = ' '.join(['\\textbullet\\ ' + x for x in takes]) or '\\textbullet\\ Validate assumptions with source backup.'
    image = _image_block(_resolve_image(section, assets, idx))
    lead_block = '{\\textcolor{BOBlue}{\\normalsize ' + lead + '}}\\par\\vspace{2pt}\n' if lead else ''
    left = '\n\n'.join(['{\\small ' + p + '}' for p in paragraphs[:2]])
    right = '\n\n'.join(['{\\small ' + p + '}' for p in paragraphs[2:4]])
    more = '\n'.join(['{\\small ' + p + '}' for p in paragraphs[4:]])
    return '\\clearpage\n' + _kicker(f'Chapter {idx}') + _heading(title) + lead_block + '\\begin{minipage}[t]{0.58\\linewidth}\n' + left + '\n\\end{minipage}\\hfill\n\\begin{minipage}[t]{0.36\\linewidth}\n' + image + '\n\\end{minipage}\n' + '\\vspace{3pt}\\noindent\\fcolorbox{BOLine}{BOLight}{\\parbox{0.965\\linewidth}{\\small \\textbf{So what:} ' + take_text + '}}\\vspace{4pt}\n' + '{\\small ' + right + '}\n' + _analysis_block(idx) + more


def _image_block(path: str) -> str:
    if path:
        return '\\includegraphics[width=58mm,height=45mm,keepaspectratio]{' + path + '}'
    return '\\fbox{\\parbox[c][45mm][c]{58mm}{\\centering\\scriptsize\\color{BOMuted} Strategic visual}}'


def _resolve_image(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    for key in [f'image-{idx}', str(section.get('visual_hint', '')), 'cover-background']:
        path = _asset_path(assets.get(key, ''))
        if path:
            return path
    return ''


def _analysis_block(idx: int) -> str:
    variants = [
        [('Market signal', 'Structural demand'), ('Capability', 'Scale and proof'), ('Execution', 'Risks and actions')],
        [('Use case', 'Priority logic'), ('Near term', 'Clear proof path'), ('Longer term', 'Monitor options')],
        [('Risk', 'Mitigation'), ('Evidence quality', 'Validate sources'), ('Adoption delay', 'Focus urgent buyers')],
    ]
    rows = variants[(idx - 1) % len(variants)]
    body = ''.join([_tex(a) + ' & ' + _tex(b) + ' \\\\ \n' for a, b in rows])
    return '\\vspace{4pt}{\\textcolor{BOBlue}{\\scriptsize\\bfseries ANALYTIC CHECKLIST}}\\par\\begin{tabularx}{\\linewidth}{p{42mm}Y}\n' + body + '\\end{tabularx}\n'


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
    return [{'title': 'Executive priorities and implications', 'lead': 'The analysis should be translated into a concise management agenda.', 'paragraphs': ['The available evidence should be organized around decision quality, execution risk and near-term management implications.', 'The most useful output is a short list of actions that can be tested against public evidence and client constraints.', 'Follow-up work should validate the assumptions against the source backup.'], 'key_takeaways': ['Focus on actionability.'], 'visual_hint': 'image-1'}]


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
