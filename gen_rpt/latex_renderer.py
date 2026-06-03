from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

PROCESS_LANGUAGE_PATTERNS = (
    r"\bthis\s+(?:chapter|section)\s+(?:therefore\s+)?(?:concludes|finds|shows|argues|frames|explains|demonstrates|sets\s+out|assesses|analyzes|translates)\s+that\b",
    r"\bthis\s+(?:chapter|section)\s+(?:therefore\s+)?(?:frames|sets\s+out|assesses|analyzes|explains|translates)\s+the\s+(?:topic|issue|question)\b",
    r"\bthe\s+(?:chapter|section)\s+(?:therefore\s+)?(?:concludes|finds|shows|argues|frames|explains|demonstrates|sets\s+out|assesses|analyzes|translates)\s+that\b",
    r"\bthis\s+(?:chapter|section)\s+is\s+about\b",
    r"\bthis\s+(?:chapter|section)\s+will\b",
)

HEADER = r'''
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=14mm,top=18mm,bottom=20mm,headheight=12pt,headsep=5mm,footskip=9mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{tabularx}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{needspace}
\usepackage{multicol}
\usepackage{enumitem}
\defaultfontfeatures{Ligatures=NoCommon}
\IfFontExistsTF{Noto Sans CJK SC}{\setmainfont{Noto Sans CJK SC}\setsansfont{Noto Sans CJK SC}}{\setmainfont{DejaVu Sans}\setsansfont{DejaVu Sans}}
\IfFontExistsTF{DejaVu Sans}{\newfontfamily\BOQuoteFont{DejaVu Sans}}{\newfontfamily\BOQuoteFont{Latin Modern Sans}}
\newcommand{\BOApos}{{\BOQuoteFont\char"0027}}
\definecolor{BOBlue}{HTML}{0E6B72}
\definecolor{BOBright}{HTML}{20A66A}
\definecolor{BOGreen}{HTML}{00A651}
\definecolor{BONavy}{HTML}{1B2A34}
\definecolor{BOText}{HTML}{24323A}
\definecolor{BOMuted}{HTML}{7C878E}
\definecolor{BOLine}{HTML}{D9E1E6}
\definecolor{BOLight}{HTML}{F2F6F4}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.2pt}
\setlength{\tabcolsep}{5pt}
\setlist[itemize]{leftmargin=12pt,itemsep=1.5pt,topsep=2pt,parsep=0pt}
\renewcommand{\arraystretch}{1.12}
\hyphenpenalty=10000
\exhyphenpenalty=10000
\emergencystretch=4em
\sloppy
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{}
\fancyhead[R]{}
\fancyfoot[L]{\scriptsize\color{BOMuted} BlueOcean}
\fancyfoot[C]{\scriptsize\color{BOMuted} Deep Research Report}
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
    title_text = str(report.get('report_title') or topic)
    title = _tex(title_text)
    summary = _summary_items(report.get('executive_summary', []))
    sections = _repair_sections(report, _safe_sections(report.get('sections', [])), topic, summary)
    charts = _safe_charts(report.get('charts', []))
    refs = report.get('reference_institutions', []) or []
    parts = [HEADER, '\\begin{document}', '\\raggedright']
    parts.append(_cover_page(title, _asset_path(assets.get('cover-background', '')), topic))
    parts.append(_agenda_and_contents_page(summary, sections, charts))
    parts.append(_opening_page(report, summary, sections, assets, topic))
    chart_index = 0
    for idx, section in enumerate(sections, start=1):
        parts.append(_chapter_block(section, assets, idx))
        if chart_index < len(charts):
            parts.append(_exhibit_page([charts[chart_index]], assets, chart_index + 1))
            chart_index += 1
    while chart_index < len(charts):
        parts.append(_exhibit_page([charts[chart_index]], assets, chart_index + 1))
        chart_index += 1
    parts.append(_disclaimer_page(refs))
    parts.append(_back_cover_page(_asset_path(assets.get('cover-background', ''))))
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, cover: str, topic: str) -> str:
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[BONavy,opacity=.10] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    prepared = _tex(date.today().isoformat())
    topic_line = _tex(_shorten(topic, 180))
    return r'''
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\fill[white,opacity=.96] ([xshift=14mm,yshift=-24mm]current page.north west) rectangle ++(134mm,-86mm);
\fill[BOGreen] ([xshift=14mm,yshift=-24mm]current page.north west) rectangle ++(134mm,-2mm);
\node[anchor=north west,text width=114mm] at ([xshift=23mm,yshift=-35mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOMuted} BLUEOCEAN RESEARCH};
\node[anchor=north west,text width=114mm] at ([xshift=23mm,yshift=-49mm]current page.north west) {\parbox{114mm}{\raggedright\sffamily\fontsize{21}{25}\selectfont\color{BONavy} ''' + title + r'''}};
\node[anchor=north west,text width=114mm] at ([xshift=23mm,yshift=-93mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOText} ''' + topic_line + r'''\\\vspace{2pt}\color{BOMuted}''' + prepared + r'''};
\node[anchor=south east] at ([xshift=-10mm,yshift=8mm]current page.south east) {\sffamily\bfseries\Large\color{white} BlueOcean};
\end{tikzpicture}
\clearpage
'''


def _agenda_and_contents_page(summary: List[str], sections: List[Dict[str, Any]], charts: List[Dict[str, Any]]) -> str:
    rows = []
    for page_no, title_raw, hints in _content_page_rows(summary, sections, charts):
        title = _tex(_shorten(title_raw, 160))
        rows.append('\\textcolor{BOGreen}{\\bfseries ' + page_no + '} & ' + title + ' \\\\[5pt]\n')
        for sub in hints[:2]:
            rows.append(' & {\\scriptsize\\color{BOMuted} ' + _tex(_display_bullet(sub, 140)) + '} \\\\[1pt]\n')
    return (
        '\\clearpage\n'
        '{\\sffamily\\fontsize{26}{31}\\selectfont\\color{BONavy} Contents}\\par\\vspace{10pt}\n'
        '\\begin{tabularx}{\\linewidth}{p{14mm}Y}\n'
        + ''.join(rows)
        + '\\end{tabularx}\n\\clearpage\n'
    )


def _content_page_rows(summary: List[str], sections: List[Dict[str, Any]], charts: List[Dict[str, Any]]) -> List[tuple[str, str, List[str]]]:
    rows: List[tuple[str, str, List[str]]] = [('03', _agenda_heading(summary, sections), [])]
    page_no = 4
    chart_count = len(charts)
    chart_pages = 0
    for idx, section in enumerate(sections, start=1):
        rows.append((f'{page_no:02d}', _strip_number_prefix(section.get('title', 'Section')), []))
        page_no += 1
        if chart_pages < chart_count:
            page_no += 1
            chart_pages += 1
    while chart_pages < chart_count:
        page_no += 1
        chart_pages += 1
    rows.append((f'{page_no:02d}', 'About the research', []))
    return rows


def _opening_page(report: Dict[str, Any], summary: List[str], sections: List[Dict[str, Any]], assets: Dict[str, str], topic: str) -> str:
    title = _tex(_shorten(_agenda_heading(summary, sections), 135))
    narrative = _normalize_punctuation(str(report.get('executive_summary_text') or '').strip())
    if not narrative:
        narrative = ' '.join(summary[:3])
    narrative = _tex(_shorten(narrative, 1200))
    visual = _asset_path(assets.get('image-1', '')) or _asset_path(assets.get('cover-background', ''))
    image = _image_strip(visual, '58mm') if visual else ''
    left = narrative
    right_source = ' '.join(summary[1:4]) or ' '.join(_strip_number_prefix(x.get('lead') or x.get('title') or '') for x in sections[:3])
    right = _tex(_shorten(right_source, 950))
    return (
        '\\clearpage\n'
        + (image + '\\vspace{0pt}\n' if image else '')
        + _green_rule('88mm')
        + '{\\sffamily\\fontsize{24}{30}\\selectfont\\color{BONavy} ' + title + '}\\par\\vspace{11pt}\n'
        + '\\begin{minipage}[t]{0.44\\linewidth}\n'
        + '{\\sffamily\\fontsize{17}{23}\\selectfont\\color{BOGreen} ' + _tex(_shorten(_agenda_heading(summary, sections), 190)) + '}\\par\\vspace{10pt}\n'
        + '{\\small\\color{BOText} ' + left + '}\\par\n'
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.45\\linewidth}\n'
        + '{\\small\\color{BOText} ' + right + '}\\par\n'
        + '\\end{minipage}\n'
    )


def _executive_summary_page(report: Dict[str, Any], summary: List[str]) -> str:
    narrative = str(report.get('executive_summary_text') or '').strip()
    if not narrative and not summary:
        return ''
    body = '\\clearpage\n' + _kicker('Executive summary') + _heading('What the CEO should take away before reading the body') + _rule()
    if narrative:
        body += '{\\normalsize\\color{BONavy} ' + _tex(_shorten(narrative, 1400)) + '}\\par\\vspace{6pt}\n'
    rows = []
    for idx, item in enumerate(summary[:6], start=1):
        rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\footnotesize ' + _tex(_shorten(item, 260)) + '} \\\\[5pt]\n')
    if rows:
        body += '\\begin{tabularx}{\\linewidth}{p{12mm}Y}\n' + ''.join(rows) + '\\end{tabularx}\n'
    return body


def _key_findings_page(report: Dict[str, Any]) -> str:
    findings = _as_list(report.get('key_findings'))[:6]
    if not findings:
        return ''
    body = '\\clearpage\n' + _kicker('Key findings') + _heading('The evidence points to a small set of management implications') + _rule()
    body += '\\begin{tabularx}{\\linewidth}{p{9mm}Y}\n'
    for idx, item in enumerate(findings, start=1):
        finding = _tex(_shorten(_field(item, 'finding'), 220))
        evidence = _tex(_shorten(_field(item, 'evidence'), 230))
        implication = _tex(_shorten(_field(item, 'management_implication'), 230))
        body += '\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\small\\bfseries ' + finding + '}\\par{\\scriptsize\\color{BOMuted} Evidence: ' + evidence + '}\\par{\\scriptsize\\color{BOMuted} Management implication: ' + implication + '} \\\\[6pt]\n'
    return body + '\\end{tabularx}\n'


def _action_plan_page(report: Dict[str, Any]) -> str:
    actions = _as_list(report.get('action_plan'))[:5]
    if not actions:
        return ''
    body = '\\clearpage\n' + _kicker('Management action plan') + _heading('Management action plan') + '{\\small\\color{BOMuted} Actions should be sequenced by evidence gates, not by market excitement.}\\par\\vspace{4pt}\n' + _rule()
    body += '\\begin{tabularx}{\\linewidth}{p{24mm}Yp{25mm}Y}\n{\\scriptsize\\bfseries\\color{BOBlue} HORIZON} & {\\scriptsize\\bfseries\\color{BOBlue} ACTION} & {\\scriptsize\\bfseries\\color{BOBlue} OWNER} & {\\scriptsize\\bfseries\\color{BOBlue} DECISION GATE} \\\\[3pt]\n'
    for item in actions:
        body += (
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'horizon'), 80)) + '} & '
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'action'), 240)) + '\\par\\textcolor{BOMuted}{' + _tex(_shorten(_field(item, 'success_metric'), 150)) + '}} & '
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'owner'), 80)) + '} & '
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'decision_gate'), 180)) + '} \\\\[6pt]\n'
        )
    return body + '\\end{tabularx}\n'


def _risk_register_page(report: Dict[str, Any]) -> str:
    risks = _as_list(report.get('risk_register'))[:6]
    if not risks:
        return ''
    body = '\\clearpage\n' + _kicker('Risk register') + _heading('Risk register') + '{\\small\\color{BOMuted} The board should track the assumptions that can break the case.}\\par\\vspace{4pt}\n' + _rule()
    body += '\\begin{tabularx}{\\linewidth}{p{32mm}Y Y}\n{\\scriptsize\\bfseries\\color{BOBlue} RISK} & {\\scriptsize\\bfseries\\color{BOBlue} TRIGGER} & {\\scriptsize\\bfseries\\color{BOBlue} MANAGEMENT ACTION} \\\\[3pt]\n'
    for item in risks:
        body += (
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'risk'), 150)) + '} & '
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'trigger'), 170)) + '} & '
            '{\\scriptsize ' + _tex(_shorten(_field(item, 'management_action'), 190)) + '\\par\\textcolor{BOMuted}{' + _tex(_shorten(_field(item, 'evidence_boundary'), 160)) + '}} \\\\[6pt]\n'
        )
    return body + '\\end{tabularx}\n'


def _scenario_page(report: Dict[str, Any]) -> str:
    scenarios = _as_list(report.get('scenario_vignettes'))[:2]
    if not scenarios:
        return ''
    body = '\\clearpage\n' + _kicker('CEO decision scenario') + _heading('The analysis must land in a concrete executive decision') + _rule()
    for item in scenarios:
        body += (
            '{\\color{BOBlue}\\sffamily\\bfseries ' + _tex(_shorten(_field(item, 'title'), 120)) + '}\\par\\vspace{2pt}\n'
            + _para(_tex(_shorten(_field(item, 'situation'), 420)))
            + '{\\small\\textbf{CEO question:} ' + _tex(_shorten(_field(item, 'ceo_question'), 240)) + '}\\par\n'
            + '{\\small\\textbf{Recommended move:} ' + _tex(_shorten(_field(item, 'recommended_move'), 280)) + '}\\par\n'
            + '{\\scriptsize\\color{BOMuted} ' + _tex(_shorten(_field(item, 'watchouts'), 260)) + '}\\par\\vspace{8pt}\n'
        )
    return body


def _methodology_page(report: Dict[str, Any], refs: List[Any]) -> str:
    note = str(report.get('methodology_note') or '').strip()
    authors = _as_list(report.get('author_credentials'))[:4]
    if not note and not authors and not refs:
        return ''
    body = '\\clearpage\n' + _kicker('Method and team') + _heading('Method and team') + '{\\small\\color{BOMuted} Source boundary, validation approach and team credentials.}\\par\\vspace{4pt}\n' + _rule()
    if note:
        body += _para(_tex(_shorten(note, 1100)))
    if authors:
        body += '\\vspace{4pt}\\begin{tabularx}{\\linewidth}{p{35mm}Y}\n'
        for item in authors:
            body += '{\\small\\bfseries ' + _tex(_shorten(_field(item, 'name'), 80)) + '}\\par{\\scriptsize\\color{BOMuted} ' + _tex(_shorten(_field(item, 'role'), 110)) + '} & {\\scriptsize ' + _tex(_shorten(_field(item, 'credentials'), 260)) + '} \\\\[6pt]\n'
        body += '\\end{tabularx}\n'
    if refs:
        body += '\\vspace{5pt}{\\scriptsize\\color{BOMuted} This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. Full source backup is retained separately.}\\par\n'
    return body


def _chapter_block(section: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title_raw = _strip_number_prefix(section.get('title', f'Section {idx}'))
    title = _tex(title_raw)
    lead_raw = str(section.get('lead', '') or '').strip()
    lead = _tex(_shorten(lead_raw, 360))
    paras = _paras(section)
    visual_path = _resolve_image(section, assets, idx)

    chapter = '\\clearpage\n\\label{chap:' + str(idx) + '}\n'
    if visual_path:
        chapter += _image_strip(visual_path, '54mm') + '\\vspace{0pt}\n'
    chapter += _green_rule('96mm')
    chapter += _heading(title)
    if lead and _normalize_punctuation(lead_raw).lower() != _normalize_punctuation(title_raw).lower():
        chapter += '{\\sffamily\\fontsize{15}{20}\\selectfont\\color{BOGreen} ' + lead + '}\\par\\vspace{9pt}\n'
    if len(paras) >= 2:
        chapter += '\\begin{multicols}{2}\n' + _paragraph_group(paras[:6]) + '\\end{multicols}\n'
    else:
        chapter += _paragraph_group(paras)
    chapter += '\\label{chap:' + str(idx) + ':end}\n'
    return chapter


def _subsection_blocks(section: Dict[str, Any], fallback_paras: List[str]) -> str:
    blocks = []
    seen = set()
    for subsection in section.get('subsections', []) or []:
        if not isinstance(subsection, dict):
            continue
        title = _strip_number_prefix(subsection.get('title', ''))
        paras = [_normalize_punctuation(str(x).strip()) for x in subsection.get('paragraphs', []) if str(x).strip()]
        paras = [p for p in paras if p.lower() not in seen]
        for p in paras:
            seen.add(p.lower())
        if not paras:
            continue
        if title and not _is_generic_title(title):
            blocks.append(_subhead(title))
        for p in paras[:5]:
            blocks.append(_para(_tex(p)))
    if not blocks:
        blocks.extend(_para(p) for p in fallback_paras[:7])
    return ''.join(blocks)


def _subhead(text: str) -> str:
    return '\\vspace{4pt}{\\color{BOBlue}\\sffamily\\bfseries\\small ' + _tex(text) + '}\\par\\vspace{1pt}\n'


def _full_width_image(path: str, width: str, height: str) -> str:
    if not path:
        return ''
    return '\\includegraphics[width=' + width + ',height=' + height + ',keepaspectratio]{' + path + '}'


def _image_strip(path: str, height: str) -> str:
    if not path:
        return ''
    return (
        '\\noindent\\begin{tikzpicture}\n'
        '\\path[use as bounding box] (0,0) rectangle (\\linewidth,' + height + ');\n'
        '\\clip (0,0) rectangle (\\linewidth,' + height + ');\n'
        '\\node[anchor=north west,inner sep=0] at (0,' + height + ') {\\includegraphics[width=\\linewidth]{' + path + '}};\n'
        '\\end{tikzpicture}\n'
    )


def _green_rule(width: str = '82mm') -> str:
    return (
        '\\noindent\\begin{tikzpicture}\n'
        '\\fill[BOGreen] (0,0) rectangle (' + width + ',1.2pt);\n'
        '\\end{tikzpicture}\\par\\vspace{8pt}\n'
    )


def _paragraph_group(paragraphs: List[str]) -> str:
    return ''.join(_para(p) for p in paragraphs if str(p).strip())


def _inline_watchouts(items: List[str]) -> str:
    bullets = ''.join('\\item ' + _tex(_shorten(item, 180)) + '\n' for item in items if str(item).strip())
    if not bullets:
        return ''
    return (
        '\\vspace{4pt}\\noindent\\begin{minipage}{\\linewidth}\n'
        '{\\textcolor{BOGreen}{\\scriptsize\\bfseries DATA NOTE}}\\par\\vspace{2pt}\n'
        '{\\footnotesize\\begin{itemize}\n' + bullets + '\\end{itemize}}\n'
        '\\end{minipage}\n'
    )


def _callout_box(items: List[str]) -> str:
    bullets = ''.join('\\item ' + _tex(_shorten(item, 130)) + '\n' for item in items if str(item).strip())
    if not bullets:
        bullets = '\\item Focus on the few facts that change the management decision.\n'
    return (
        '\\fcolorbox{BOLine}{BOLight}{\\begin{minipage}[t][44mm][t]{0.95\\linewidth}'
        '\\vspace{4pt}{\\textcolor{BOGreen}{\\scriptsize\\bfseries DATA NOTE}}\\par'
        '\\vspace{2pt}{\\footnotesize\\begin{itemize}\n' + bullets + '\\end{itemize}}'
        '\\end{minipage}}'
    )


def _exhibit_page(charts: List[Dict[str, Any]], assets: Dict[str, str], start_idx: int) -> str:
    blocks: List[str] = ['\\clearpage\n']
    for offset, chart in enumerate(charts):
        idx = start_idx + offset
        blocks.append(_exhibit_block(chart, assets, idx, compact=len(charts) > 1))
        if offset < len(charts) - 1:
            blocks.append('\\vspace{9pt}\\textcolor{BOLine}{\\rule{\\linewidth}{0.3pt}}\\vspace{8pt}\n')
    return ''.join(blocks)


def _exhibit_block(chart: Dict[str, Any], assets: Dict[str, str], idx: int, *, compact: bool = False) -> str:
    title = _tex(_chart_title(chart.get('title') or f'Exhibit {idx}', 130))
    subtitle_raw = _normalize_punctuation(str(chart.get('subtitle') or chart.get('caption') or ''))
    caption_raw = _normalize_punctuation(str(chart.get('caption') or ''))
    if caption_raw.lower() == subtitle_raw.lower():
        caption_raw = ''
    subtitle = _tex(_shorten(subtitle_raw, 220))
    caption = _tex(_shorten(caption_raw, 280))
    source = _tex(_shorten(chart.get('source_note') or 'Source: public sources and BlueOcean synthesis.', 190))
    visual = _native_chart(chart, compact=compact)
    if not visual:
        path = _asset_path(assets.get(str(chart.get('id') or f'chart-{idx}'), '')) or _resolve_chart(assets, idx)
        visual = _center_image(path, '0.96\\linewidth', '84mm' if compact else '132mm') if path else _callout_box([caption or subtitle])
    return (
        '{\\textcolor{BOGreen}{\\scriptsize\\bfseries EXHIBIT ' + str(idx) + '}}\\par\\vspace{4pt}\n'
        + '{\\sffamily\\fontsize{17}{21}\\selectfont\\color{BONavy} ' + title + '}\\par\n'
        + ('{\\small\\color{BOText} ' + subtitle + '}\\par\\vspace{6pt}\n' if subtitle else '\\vspace{6pt}\n')
        + visual
        + ('\\vspace{4pt}{\\footnotesize\\color{BOText} ' + caption + '}\\par\n' if caption else '')
        + '{\\scriptsize\\color{BOMuted} ' + source + '}\\par\n'
    )


def _native_chart(chart: Dict[str, Any], *, compact: bool) -> str:
    chart_type = str(chart.get('type') or '').lower()
    if chart_type in {'scatter', 'risk_matrix', 'quadrant'}:
        chart_type = 'bubble'
    elif chart_type in {'heatmap', 'table', 'scorecard'}:
        chart_type = 'matrix'
    elif chart_type in {'pie', 'donut', 'column'}:
        chart_type = 'bar'
    if chart_type == 'stacked_bar':
        return _native_stacked_bar(chart, compact=compact)
    if chart_type in {'bar', 'column'}:
        return _native_bar(chart, compact=compact)
    if chart_type == 'line':
        return _native_line(chart, compact=compact)
    if chart_type == 'matrix':
        return _native_matrix(chart, compact=compact)
    if chart_type == 'bubble':
        return _native_bubble(chart, compact=compact)
    if chart.get('series') and chart.get('categories'):
        return _native_bar(chart, compact=compact)
    if chart.get('rows') and chart.get('columns') and chart.get('values'):
        return _native_matrix(chart, compact=compact)
    return ''


def _native_bar(chart: Dict[str, Any], *, compact: bool) -> str:
    categories = _chart_categories(chart, 6 if compact else 8)
    series = _series_payload(chart)[:3]
    if not categories or not series:
        return ''
    max_value = max([abs(v) for item in series for v in item['values'][:len(categories)]] + [1.0])
    width, height = (13.2, 3.4) if compact else (16.2, 7.4)
    chart_h = height - 1.35
    left = 0.7
    group_w = (width - left - .35) / max(1, len(categories))
    bar_w = min(.72 if not compact else .44, group_w / max(1, len(series) + .65))
    colors = ['BOGreen', 'BOBlue', 'BONavy']
    body = [_tikz_begin(width, height), f'\\draw[BOLine] ({left:.2f},0.72) -- ({width - .2:.2f},0.72);\n']
    if not compact:
        for tick in (0.25, 0.5, 0.75, 1.0):
            y = 0.72 + tick * chart_h
            body.append(f'\\draw[BOLine,opacity=.45] ({left:.2f},{y:.2f}) -- ({width - .25:.2f},{y:.2f});\n')
    for ci, category in enumerate(categories):
        base_x = left + ci * group_w + group_w * .15
        for si, item in enumerate(series):
            value = item['values'][ci] if ci < len(item['values']) else 0.0
            h = max(.04, abs(value) / max_value * chart_h)
            x = base_x + si * bar_w
            body.append(f'\\fill[{colors[si % len(colors)]}] ({x:.2f},0.72) rectangle ({x + bar_w * .78:.2f},{0.72 + h:.2f});\n')
            if not compact:
                body.append(f'\\node[anchor=south,align=center] at ({x + bar_w * .39:.2f},{0.78 + h:.2f}) {{\\scriptsize {_tex(_format_value_label(value))}}};\n')
        body.append(f'\\node[anchor=north,align=center,text width={group_w * .92:.2f}cm] at ({base_x + group_w * .36:.2f},0.55) {{\\scriptsize {_tex(_shorten(category, 22))}}};\n')
    body.extend(_legend(series, colors, y=height - .22))
    body.append(_tikz_end())
    return ''.join(body)


def _native_stacked_bar(chart: Dict[str, Any], *, compact: bool) -> str:
    categories = _chart_categories(chart, 5 if compact else 7)
    series = _series_payload(chart)[:5]
    if not categories or not series:
        return ''
    totals = [sum(max(0.0, item['values'][ci] if ci < len(item['values']) else 0.0) for item in series) for ci in range(len(categories))]
    max_value = max(totals + [1.0])
    width, height = (13.2, 3.5) if compact else (16.2, 7.3)
    chart_h = height - 1.35
    left = 0.8
    group_w = (width - left - .4) / max(1, len(categories))
    bar_w = min(.78 if not compact else .62, group_w * .55)
    colors = ['BOGreen', 'BOBlue', 'BONavy', 'BOMuted', 'BOLine']
    body = [_tikz_begin(width, height), f'\\draw[BOLine] ({left:.2f},0.72) -- ({width - .2:.2f},0.72);\n']
    for ci, category in enumerate(categories):
        x = left + ci * group_w + group_w * .26
        y = .72
        for si, item in enumerate(series):
            value = max(0.0, item['values'][ci] if ci < len(item['values']) else 0.0)
            h = value / max_value * chart_h
            if h > 0:
                body.append(f'\\fill[{colors[si % len(colors)]}] ({x:.2f},{y:.2f}) rectangle ({x + bar_w:.2f},{y + h:.2f});\n')
                y += h
        if not compact:
            body.append(f'\\node[anchor=south,align=center] at ({x + bar_w / 2:.2f},{y + .05:.2f}) {{\\scriptsize {_tex(_format_value_label(totals[ci]))}}};\n')
        body.append(f'\\node[anchor=north,align=center,text width={group_w * .92:.2f}cm] at ({x + bar_w / 2:.2f},0.55) {{\\scriptsize {_tex(_shorten(category, 22))}}};\n')
    body.extend(_legend(series, colors, y=height - .22))
    body.append(_tikz_end())
    return ''.join(body)


def _native_line(chart: Dict[str, Any], *, compact: bool) -> str:
    categories = _chart_categories(chart, 6 if compact else 8)
    series = _series_payload(chart)[:3]
    if len(categories) < 2 or not series:
        return ''
    max_value = max([abs(v) for item in series for v in item['values'][:len(categories)]] + [1.0])
    width, height = (13.2, 3.6) if compact else (16.2, 7.2)
    left, bottom = .8, .78
    chart_w, chart_h = width - 1.25, height - 1.45
    colors = ['BOGreen', 'BOBlue', 'BONavy']
    body = [_tikz_begin(width, height), f'\\draw[BOLine] ({left:.2f},{bottom:.2f}) -- ({left + chart_w:.2f},{bottom:.2f});\n']
    if not compact:
        for tick in (0.25, 0.5, 0.75, 1.0):
            y = bottom + tick * chart_h
            body.append(f'\\draw[BOLine,opacity=.45] ({left:.2f},{y:.2f}) -- ({left + chart_w:.2f},{y:.2f});\n')
    for si, item in enumerate(series):
        pts = []
        for ci in range(len(categories)):
            value = item['values'][ci] if ci < len(item['values']) else 0.0
            x = left + (chart_w * ci / max(1, len(categories) - 1))
            y = bottom + (abs(value) / max_value * chart_h)
            pts.append((x, y))
        body.append('\\draw[' + colors[si % len(colors)] + ',line width=1.1pt] ' + ' -- '.join(f'({x:.2f},{y:.2f})' for x, y in pts) + ';\n')
        for point_idx, (x, y) in enumerate(pts):
            body.append(f'\\fill[{colors[si % len(colors)]}] ({x:.2f},{y:.2f}) circle (.045);\n')
            if not compact and (point_idx == len(pts) - 1 or len(categories) <= 4):
                value = item['values'][point_idx] if point_idx < len(item['values']) else 0.0
                body.append(f'\\node[anchor=south,align=center] at ({x:.2f},{y + .08:.2f}) {{\\scriptsize {_tex(_format_value_label(value))}}};\n')
    for ci, category in enumerate(categories):
        x = left + (chart_w * ci / max(1, len(categories) - 1))
        body.append(f'\\node[anchor=north,align=center,text width=1.8cm] at ({x:.2f},{bottom - .12:.2f}) {{\\scriptsize {_tex(_shorten(category, 18))}}};\n')
    body.extend(_legend(series, colors, y=height - .18))
    body.append(_tikz_end())
    return ''.join(body)


def _native_bubble(chart: Dict[str, Any], *, compact: bool) -> str:
    points = _bubble_points(chart)[:6 if compact else 8]
    if not points:
        return ''
    width, height = (13.2, 3.5) if compact else (15.6, 5.2)
    left, bottom = .85, .65
    chart_w, chart_h = width - 1.35, height - 1.25
    body = [_tikz_begin(width, height)]
    body.append(f'\\draw[BOLine] ({left:.2f},{bottom:.2f}) -- ({left + chart_w:.2f},{bottom:.2f}) -- ({left + chart_w:.2f},{bottom + chart_h:.2f});\n')
    body.append(f'\\draw[BOLine,dashed] ({left:.2f},{bottom + chart_h / 2:.2f}) -- ({left + chart_w:.2f},{bottom + chart_h / 2:.2f});\n')
    body.append(f'\\draw[BOLine,dashed] ({left + chart_w / 2:.2f},{bottom:.2f}) -- ({left + chart_w / 2:.2f},{bottom + chart_h:.2f});\n')
    y_lanes = [-.34, -.16, .03, .21, .39, -.50, .56, -.66]
    lane_by_index = {
        original_idx: y_lanes[lane_idx % len(y_lanes)]
        for lane_idx, (original_idx, _) in enumerate(sorted(enumerate(points), key=lambda item: (_num(item[1].get('y'), 50), _num(item[1].get('x'), 50))))
    }
    for point_idx, point in enumerate(points):
        x = left + _num(point.get('x'), 50) / 100 * chart_w
        y = bottom + _num(point.get('y'), 50) / 100 * chart_h
        radius = .08 + min(80, max(10, _num(point.get('size'), 35))) / 500
        body.append(f'\\fill[BOGreen,opacity=.65] ({x:.2f},{y:.2f}) circle ({radius:.2f});\n')
        anchor = 'west' if x < left + chart_w * .70 else 'east'
        align = 'left' if anchor == 'west' else 'right'
        dx = (.18 + radius) if anchor == 'west' else -(.18 + radius)
        dy = lane_by_index.get(point_idx, 0.0)
        text_width = '2.10cm' if compact else '2.55cm'
        body.append(f'\\node[anchor={anchor},align={align},text width={text_width}] at ({x + dx:.2f},{y + dy:.2f}) {{\\scriptsize {_tex(_shorten(point.get("label", ""), 24))}}};\n')
    x_label = _tex(_shorten(chart.get('x_label') or 'Likelihood', 24))
    y_label = _tex(_shorten(chart.get('y_label') or 'Impact', 24))
    body.append(f'\\node[anchor=north east] at ({left + chart_w:.2f},{bottom - .12:.2f}) {{\\scriptsize {x_label}}};\n')
    body.append(f'\\node[anchor=center,rotate=90] at ({left - .30:.2f},{bottom + chart_h / 2:.2f}) {{\\scriptsize {y_label}}};\n')
    body.append(_tikz_end())
    return ''.join(body)


def _bubble_points(chart: Dict[str, Any]) -> List[Dict[str, Any]]:
    points = [dict(p) for p in _as_list(chart.get('points')) if isinstance(p, dict)]
    converted: List[Dict[str, Any]] = []
    for row in _point_rows_from_chart_data({'datasets': chart.get('datasets')} if chart.get('datasets') else None):
        label = row.get('label') or row.get('risk') or row.get('name') or row.get('category') or row.get('driver') or ''
        x = row.get('x', row.get('likelihood', row.get('probability', row.get('readiness', row.get('attractiveness', 50)))))
        y = row.get('y', row.get('impact', row.get('severity', row.get('importance', row.get('return', 50)))))
        size = row.get('size', row.get('impact', row.get('severity', row.get('importance', 45))))
        converted.append({'label': label, 'x': _scale_point(x), 'y': _scale_point(y), 'size': _scale_point(size)})
    for row in _point_rows_from_chart_data(chart.get('data')):
        label = row.get('label') or row.get('risk') or row.get('name') or row.get('category') or row.get('driver') or ''
        x = row.get('x', row.get('likelihood', row.get('probability', row.get('readiness', row.get('attractiveness', 50)))))
        y = row.get('y', row.get('impact', row.get('severity', row.get('importance', row.get('return', 50)))))
        size = row.get('size', row.get('impact', row.get('severity', row.get('importance', 45))))
        converted.append({'label': label, 'x': _scale_point(x), 'y': _scale_point(y), 'size': _scale_point(size)})
    if points and not _weak_bubble_points(points):
        return points
    if converted and not _weak_bubble_points(converted):
        return converted
    return points or converted


def _scale_point(value: Any) -> float:
    number = _num(value, 50)
    if number <= 5:
        return number * 20
    if number <= 10:
        return number * 10
    return max(0, min(100, number))


def _native_matrix(chart: Dict[str, Any], *, compact: bool) -> str:
    rows = [str(x) for x in _as_list(chart.get('rows'))][:5 if compact else 7]
    cols = [str(x) for x in _as_list(chart.get('columns'))][:4]
    values = _as_list(chart.get('values'))
    if not rows or not cols:
        return ''
    col_spec = 'p{38mm}' + ''.join('>{\\centering\\arraybackslash}p{21mm}' for _ in cols)
    lines = ['{\\scriptsize\\begin{tabular}{' + col_spec + '}\n']
    lines.append(' & ' + ' & '.join('{\\bfseries ' + _tex(_shorten(c, 18)) + '}' for c in cols) + ' \\\\\n\\hline\n')
    for ri, row in enumerate(rows):
        row_values = values[ri] if ri < len(values) and isinstance(values[ri], list) else []
        cells = []
        for ci in range(len(cols)):
            value = row_values[ci] if ci < len(row_values) else ''
            cells.append(_matrix_cell(value))
        lines.append('{\\bfseries ' + _tex(_shorten(row, 32)) + '} & ' + ' & '.join(cells) + ' \\\\\n')
    lines.append('\\end{tabular}}\\par\\vspace{2pt}\n')
    return ''.join(lines)


def _tikz_begin(width: float, height: float) -> str:
    return (
        '\\noindent\\begin{tikzpicture}[x=1cm,y=1cm]\n'
        f'\\path[use as bounding box] (0,0) rectangle ({width:.2f},{height:.2f});\n'
    )


def _tikz_end() -> str:
    return '\\end{tikzpicture}\\par\\vspace{2pt}\n'


def _legend(series: List[Dict[str, Any]], colors: List[str], *, y: float) -> List[str]:
    items: List[str] = []
    x = .8
    for idx, item in enumerate(series[:4]):
        name = _tex(_shorten(item.get('name') or f'Series {idx + 1}', 20))
        color = colors[idx % len(colors)]
        items.append(f'\\fill[{color}] ({x:.2f},{y:.2f}) rectangle ({x + .16:.2f},{y + .10:.2f});\n')
        items.append(f'\\node[anchor=west] at ({x + .22:.2f},{y + .05:.2f}) {{\\scriptsize {name}}};\n')
        x += min(3.2, .85 + len(str(item.get('name') or 'Series')) * .095)
    return items


def _series_payload(chart: Dict[str, Any]) -> List[Dict[str, Any]]:
    top_payload = _series_from_chart_data({'datasets': chart.get('datasets')} if chart.get('datasets') else None)
    if top_payload:
        return top_payload
    payload: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(chart.get('series')), start=1):
        if not isinstance(item, dict):
            continue
        values = [_num(x, 0.0) for x in _as_list(item.get('values'))]
        if not values:
            continue
        payload.append({'name': str(item.get('name') or f'Series {idx}'), 'values': values})
    if not payload:
        payload = _series_from_chart_data(chart.get('data'))
    if not payload:
        values = [_num(x, 0.0) for x in _as_list(chart.get('values'))]
        if values:
            payload.append({'name': str(chart.get('name') or 'Value'), 'values': values})
    return payload


def _chart_categories(chart: Dict[str, Any], limit: int) -> List[str]:
    label_categories = [str(x) for x in _as_list(chart.get('labels')) if str(x).strip()]
    if label_categories:
        return label_categories[:limit]
    categories = [str(x) for x in _as_list(chart.get('categories')) if str(x).strip()]
    data = chart.get('data')
    if not categories and isinstance(data, dict):
        categories = [str(x) for x in _as_list(data.get('labels') or data.get('categories')) if str(x).strip()]
    return categories[:limit]


def _series_from_chart_data(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    payload: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value.get('datasets') or value.get('series')), start=1):
        if not isinstance(item, dict):
            continue
        raw_values = item.get('values')
        if raw_values is None:
            raw_values = item.get('data')
        values = []
        for raw in _as_list(raw_values):
            if isinstance(raw, dict):
                raw = raw.get('y', raw.get('value', raw.get('amount', raw.get('score'))))
            if raw is not None:
                values.append(_num(raw, 0.0))
        if values:
            payload.append({'name': str(item.get('label') or item.get('name') or f'Series {idx}'), 'values': values})
    return payload


def _point_rows_from_chart_data(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def visit(node: Any, dataset_label: str = '') -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, dataset_label)
            return
        if not isinstance(node, dict):
            return
        datasets = [x for x in _as_list(node.get('datasets')) if isinstance(x, dict)]
        if datasets:
            for dataset in datasets:
                nested = dataset.get('points')
                if nested is None:
                    nested = dataset.get('data')
                if nested is None:
                    nested = dataset.get('values')
                visit(nested, str(dataset.get('label') or dataset.get('name') or dataset_label or '').strip())
            return
        nested_points = node.get('points')
        if isinstance(nested_points, list):
            visit(nested_points, dataset_label)
            return
        nested_data = node.get('data')
        if isinstance(nested_data, list) and any(isinstance(x, dict) for x in nested_data):
            visit(nested_data, dataset_label)
            return
        if not any(key in node for key in ('x', 'y', 'likelihood', 'probability', 'readiness', 'attractiveness', 'impact', 'severity', 'importance', 'return', 'size')):
            return
        row = dict(node)
        if dataset_label and not any(row.get(key) for key in ('label', 'risk', 'name', 'category', 'driver')):
            row['label'] = dataset_label
        rows.append(row)

    visit(value)
    return rows


def _weak_bubble_points(points: List[Dict[str, Any]]) -> bool:
    if len(points) < 3:
        return True
    labels = [str(point.get('label') or '').strip() for point in points]
    placeholder_count = sum(1 for label in labels if re.fullmatch(r'(point|item)[\s_#-]*\d*', label, flags=re.I))
    return placeholder_count >= max(2, len(labels) - 1)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str):
            cleaned = value.replace('%', '').replace('$', '').replace(',', '').strip()
            return float(cleaned)
        return float(value)
    except Exception:
        return default


def _format_value_label(value: Any) -> str:
    number = _num(value, 0.0)
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def _matrix_cell(value: Any) -> str:
    number = _num(value, None)
    if number is None:
        return _tex(_shorten(value, 18))
    if abs(number - round(number)) < 0.01:
        label = str(int(round(number)))
    else:
        label = f'{number:.1f}'
    color = 'BOGreen' if number >= 4 else ('BOBlue' if number >= 3 else 'BOLight')
    text_color = 'white' if color in {'BOGreen', 'BOBlue'} else 'BOText'
    return '\\colorbox{' + color + '}{\\textcolor{' + text_color + '}{\\strut\\hspace{5pt}' + _tex(label) + '\\hspace{5pt}}}'


def _decision_story_page(report: Dict[str, Any]) -> str:
    scenarios = _as_list(report.get('scenario_vignettes'))[:1]
    if scenarios and isinstance(scenarios[0], dict):
        item = scenarios[0]
        title = _field(item, 'title') or 'A boardroom question'
        situation = _field(item, 'situation')
        question = _field(item, 'ceo_question')
        move = _field(item, 'recommended_move')
        watchouts = _field(item, 'watchouts')
    else:
        title = 'A boardroom choice'
        situation = 'Leadership must decide which moves can be made now and which should wait for stronger evidence.'
        question = 'What should be done before the next major commitment?'
        move = 'Keep learning options open while reserving larger commitments for verified proof points.'
        watchouts = 'Avoid treating market enthusiasm as a substitute for validated economics.'
    if _looks_like_internal_label(title):
        title = 'A boardroom choice'
    return (
        '\\clearpage\n'
        + '{\\sffamily\\fontsize{22}{27}\\selectfont\\color{BONavy} ' + _tex(_shorten(title, 100)) + '}\\par\\vspace{10pt}\n'
        + '\\begin{minipage}[t]{0.43\\linewidth}\n'
        + '{\\sffamily\\fontsize{17}{22}\\selectfont\\color{BOGreen} ' + _tex(_shorten(question, 300)) + '}\\par\n'
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.50\\linewidth}\n'
        + _para(_tex(_shorten(situation, 620)))
        + '{\\small\\bfseries\\color{BOText} ' + _tex(_shorten(move, 360)) + '}\\par\\vspace{5pt}\n'
        + '{\\footnotesize\\color{BOMuted} ' + _tex(_shorten(watchouts, 320)) + '}\\par'
        + '\\end{minipage}\n'
    )


def _leadership_agenda_page(report: Dict[str, Any], sections: List[Dict[str, Any]]) -> str:
    actions = _as_list(report.get('action_plan'))[:4]
    risks = _as_list(report.get('risk_register'))[:4]
    action_items = []
    for item in actions:
        action_items.append(_field(item, 'action') or _field(item, 'recommended_move') or _item_to_text(item))
    if not action_items:
        action_items = [_strip_number_prefix(section.get('lead') or section.get('title') or '') for section in sections[:4]]
    risk_items = []
    for item in risks:
        trigger = _field(item, 'trigger')
        action = _field(item, 'management_action')
        risk = _field(item, 'risk')
        parts = []
        if risk:
            parts.append(risk)
        if trigger:
            parts.append('Watch for ' + trigger[0].lower() + trigger[1:] if len(trigger) > 1 else trigger.lower())
        if action:
            parts.append(action)
        risk_items.append(_join_sentence_fragments(parts))
    left = ''.join('\\item ' + _tex(_shorten(x, 230)) + '\n' for x in action_items if str(x).strip())
    right = ''.join('\\item ' + _tex(_shorten(x, 230)) + '\n' for x in risk_items if str(x).strip())
    return (
        '\\clearpage\n'
        + '{\\sffamily\\fontsize{21}{26}\\selectfont\\color{BONavy} Board implications}\\par\\vspace{7pt}\n'
        + '\\begin{minipage}[t]{0.47\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries PRIORITIES}}\\par\\vspace{3pt}\n'
        + '{\\small\\begin{itemize}\n' + (left or '\\item Convert the report into a short list of decisions, owners and proof points.\n') + '\\end{itemize}}\n'
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.45\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries EXTERNAL FACTS}}\\par\\vspace{3pt}\n'
        + '{\\small\\begin{itemize}\n' + (right or '\\item Revisit the thesis when the public evidence base, economics or regulatory path changes.\n') + '\\end{itemize}}\n'
        + '\\end{minipage}\n'
    )


def _disclaimer_page(refs: List[Any]) -> str:
    reference_note = ''
    if refs:
        reference_note = 'This report was informed by public research and data from: ' + _tex(', '.join(str(x) for x in refs)) + '. The detailed source backup is retained in the backup folder rather than reproduced in the client-facing document. '
    body = (
        'This report has been prepared for strategy discussion and executive decision support. It is not investment, legal, tax, audit or valuation advice. '
        'Market estimates, forecasts and scenarios are directional and should be independently validated before they are used for investment, financing, transaction, regulatory or operational decisions. '
        'Forward-looking views may change as technology, policy, financing, regulation, competition, supply chains and macro conditions evolve. '
        + reference_note +
        'Recipients should perform their own diligence and treat this report as one input into a broader decision process.'
    )
    return '\\clearpage\n{\\sffamily\\fontsize{20}{25}\\selectfont\\color{BONavy} About the research}\\par\\vspace{6pt}\n' + _rule() + '{\\footnotesize\\color{BOMuted} ' + body + '}\n'


def _back_cover_page(cover: str) -> str:
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[black,opacity=.45] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    return r'''
\clearpage
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\node[anchor=south east] at ([xshift=-11mm,yshift=10mm]current page.south east) {\sffamily\bfseries\Large\color{white} BlueOcean};
\end{tikzpicture}
'''


def _repair_sections(report: Dict[str, Any], sections: List[Dict[str, Any]], topic: str, summary: List[str]) -> List[Dict[str, Any]]:
    report_title = str(report.get('report_title') or topic)
    if not sections:
        sections = [{'title': report_title, 'paragraphs': []}]
    repaired: List[Dict[str, Any]] = []
    for idx, original in enumerate(sections, start=1):
        section = dict(original)
        current_title = _strip_number_prefix(section.get('title', ''))
        if _is_generic_title(current_title):
            current_title = _derive_title(idx, report_title, summary)
            section['title'] = current_title
        lead = str(section.get('lead') or '').strip()
        if not lead or _is_generic_title(lead) or _normalize_punctuation(lead).lower() == _normalize_punctuation(current_title).lower():
            section['lead'] = _derive_lead(idx, current_title, report_title, summary)
        paragraphs = _dedupe([str(x).strip() for x in section.get('paragraphs', []) if str(x).strip()])
        paragraphs = [p for p in paragraphs if not _is_bad_template_text(p)]
        if len(paragraphs) < 2:
            paragraphs = _dedupe(paragraphs + _section_fallback_paragraphs(idx, current_title, report_title, summary))
        section['paragraphs'] = [_normalize_punctuation(p) for p in paragraphs[:5]]
        repaired.append(section)
    return repaired[:10]


def _merge_sections(sections: List[Dict[str, Any]], max_sections: int, report_title: str, summary: List[str]) -> List[Dict[str, Any]]:
    if len(sections) <= max_sections:
        return sections
    group_size = (len(sections) + max_sections - 1) // max_sections
    merged: List[Dict[str, Any]] = []
    for start in range(0, len(sections), group_size):
        group = sections[start:start + group_size]
        idx = len(merged) + 1
        primary = group[0]
        merged_title = primary.get('title') or _derive_title(idx, report_title, summary)
        merged_lead = primary.get('lead') or _derive_lead(idx, merged_title, report_title, summary)
        paragraphs: List[str] = []
        subsections: List[Dict[str, Any]] = []
        for item in group:
            item_title = _strip_number_prefix(item.get('title', ''))
            item_paras = _dedupe([str(x) for x in item.get('paragraphs', []) if str(x).strip()])
            paragraphs.extend(item_paras[:5])
            if item_title and not _is_generic_title(item_title):
                subsections.append({'title': item_title, 'paragraphs': item_paras[:6]})
        paragraphs = _dedupe(paragraphs + _section_fallback_paragraphs(idx, merged_title, report_title, summary))[:10]
        merged.append({'id': primary.get('id', f'section-{idx}'), 'title': merged_title, 'lead': merged_lead, 'paragraphs': paragraphs, 'subsections': subsections[:4], 'visual_hint': primary.get('visual_hint', f'image-{idx}')})
    return merged[:max_sections]


def _derive_title(idx: int, report_title: str, summary: List[str]) -> str:
    candidates = [s for s in summary if s and not s.lower().startswith(('this report', 'the report', 'our analysis'))]
    if idx - 1 < len(candidates):
        return _title_from_sentence(candidates[idx - 1])
    fallback = [
        'Commercial readiness will arrive later than investor enthusiasm implies',
        'Private capital is accelerating the learning curve but cannot replace engineering proof',
        'Cost competitiveness depends on deployment scale, not scientific progress alone',
        'Fuel, materials and regulation remain the constraints that can reset timing',
        'Strategic positioning should focus on options, partnerships and milestone discipline',
        'Winners will be defined by execution credibility rather than technology narratives',
    ]
    return fallback[(idx - 1) % len(fallback)]


def _title_from_sentence(text: str) -> str:
    cleaned = _normalize_punctuation(text).strip()
    cleaned = re.sub(r'^key findings?:\s*', '', cleaned, flags=re.I)
    cleaned = cleaned.split(';')[0].strip()
    if len(cleaned) > 118:
        cleaned = _compact_headline(cleaned)
    return cleaned or 'The management agenda should be staged around evidence quality'


def _derive_lead(idx: int, title: str, report_title: str, summary: List[str]) -> str:
    if idx - 1 < len(summary):
        return _shorten(summary[idx - 1], 300)
    return 'The central question is how quickly the market can convert technical progress into bankable deployment evidence.'


def _generated_paragraphs(idx: int, title: str, report_title: str, summary: List[str]) -> List[str]:
    return _section_fallback_paragraphs(idx, title, report_title, summary)


def _section_fallback_paragraphs(idx: int, title: str, report_title: str, summary: List[str]) -> List[str]:
    topic = _normalize_punctuation(report_title)
    thesis = _normalize_punctuation(summary[(idx - 1) % len(summary)]) if summary else _normalize_punctuation(title)
    if not thesis:
        thesis = 'The question for leadership is where the evidence is strong enough to support action.'
    return [
        thesis,
        'For a CEO, the practical test is whether this changes timing, partner selection, capital exposure or the next decision milestone. The answer should be staged around verified operating evidence rather than broad market enthusiasm.',
        'The stronger posture is to keep learning options open while reserving larger commitments for facts that would change the business case: cost, reliability, supply depth, regulatory clarity and customer willingness to sign binding commitments.',
        'In the context of ' + topic + ', management should separate moves that create privileged learning from moves that only create headline exposure. That distinction keeps the organization close to the opportunity without forcing premature conviction.',
    ]


def _is_bad_template_text(text: str) -> bool:
    cleaned = _normalize_punctuation(text).lower()
    bad_markers = [
        'the issue matters because',
        'the first lens is technology readiness',
        'the second lens is economics',
        'the third lens is ecosystem readiness',
        'this chapter therefore frames the topic',
        'this chapter concludes',
        'this section concludes',
        'this chapter finds',
        'this section finds',
        'this chapter shows',
        'this section shows',
        'management should prioritize evidence quality',
    ]
    return any(marker in cleaned for marker in bad_markers) or any(re.search(pattern, cleaned, flags=re.I) for pattern in PROCESS_LANGUAGE_PATTERNS)


def _paragraphs_are_weak(paragraphs: List[str]) -> bool:
    if len(paragraphs) < 4:
        return True
    joined = ' '.join(paragraphs).lower()
    weak_markers = ['section 1', 'section 2', 'section 3', 'section 4', 'resource allocation from signals', 'single binary bet', 'measurable proof points']
    if sum(joined.count(marker) for marker in weak_markers) >= 2:
        return True
    unique = set(p.strip().lower() for p in paragraphs)
    return len(unique) <= max(2, len(paragraphs) // 2)


def _is_generic_title(text: str) -> bool:
    cleaned = _normalize_punctuation(str(text or '')).strip().lower()
    return bool(re.match(r'^(section|chapter)\s*\d*$', cleaned)) or cleaned in {'section', 'chapter', 'executive priorities and implications'}


def _dedupe(values: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        normalized = _normalize_punctuation(str(value).strip())
        if not normalized:
            continue
        if _is_bad_template_text(normalized):
            continue
        key = re.sub(r'\W+', '', normalized.lower())[:180]
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _agenda_heading(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    for item in summary:
        cleaned = _normalize_punctuation(str(item or '').strip())
        if not cleaned:
            continue
        if cleaned.lower().startswith(('this report', 'the report', 'our analysis')):
            continue
        return _compact_headline(cleaned)
    if sections:
        return _shorten(_strip_number_prefix(sections[0].get('title', 'Management agenda')), 135)
    return 'Management should focus on the few moves that can change the outcome'


def _compact_headline(text: str) -> str:
    cleaned = _normalize_punctuation(text)
    cleaned = re.split(r'(?<=[.!?])\s+', cleaned, maxsplit=1)[0].strip()
    for pattern in [
        r',\s+driven\s+by\b',
        r',\s+supported\s+by\b',
        r',\s+requiring\b',
        r',\s+creating\b',
        r',\s+with\b',
        r',\s+but\b',
        r',\s+while\b',
        r';',
        r'\s+because\s+',
        r'\s+but\s+',
        r'\s+while\s+',
    ]:
        parts = re.split(pattern, cleaned, maxsplit=1, flags=re.I)
        if len(parts) > 1 and 35 <= len(parts[0]) <= 145:
            return _clean_sentence_fragment(parts[0])
    return _shorten(cleaned, 110)


def _clean_sentence_fragment(text: str) -> str:
    cleaned = _normalize_punctuation(text).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = cleaned.rstrip('.,;: ')
    weak_tail = r'\s+(?:a|an|the|not|before|after|with|and|or|of|for|to|in|at|by|from|as|but|while|because|requiring|including|than)$'
    while re.search(weak_tail, cleaned, flags=re.I):
        cleaned = re.sub(weak_tail, '', cleaned, flags=re.I).rstrip('.,;: ')
    return cleaned


def _join_sentence_fragments(parts: List[str]) -> str:
    fragments = [_clean_sentence_fragment(part) for part in parts if _clean_sentence_fragment(part)]
    if not fragments:
        return ''
    return '. '.join(fragments) + '.'


def _display_bullet(text: str, max_chars: int) -> str:
    cleaned = _clean_sentence_fragment(str(text or ''))
    if len(cleaned) <= max_chars:
        return cleaned
    compact = _compact_headline(cleaned)
    if compact and len(compact) < len(cleaned):
        cleaned = compact
    return _shorten(cleaned, max_chars)


def _chart_title(value: Any, max_chars: int) -> str:
    cleaned = _clean_sentence_fragment(str(value or ''))
    compact = re.sub(
        r'^(.{8,60}?)\s+for\s+.+?\s+(should|must|will|still|depends|requires|needs|is|are)\s+(.+)$',
        lambda match: f"{match.group(1)} {match.group(2)} {match.group(3)}",
        cleaned,
        flags=re.I,
    )
    if compact != cleaned:
        cleaned = _clean_sentence_fragment(compact)
    if len(cleaned) <= max_chars:
        return cleaned
    return _display_bullet(cleaned, max_chars)


def _paras(section: Dict[str, Any]) -> List[str]:
    raw = [str(x) for x in section.get('paragraphs', []) if str(x).strip()]
    raw = _dedupe(raw)
    if not raw:
        for subsection in section.get('subsections', []) or []:
            if isinstance(subsection, dict):
                raw.extend(str(x) for x in subsection.get('paragraphs', []) if str(x).strip())
        raw = _dedupe(raw)
    if not raw and section.get('lead'):
        raw = [str(section.get('lead'))]
    return [_tex(x) for x in raw[:7]]


def _para(text: str) -> str:
    return '{\\small ' + text + '}\\par\n'


def _center_image(path: str, width: str, height: str) -> str:
    if not path:
        return ''
    return '\\begin{center}\\includegraphics[width=' + width + ',height=' + height + ',keepaspectratio]{' + path + '}\\end{center}\n'


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


def _safe_charts(value: Any) -> List[Dict[str, Any]]:
    charts = []
    for idx, item in enumerate(_as_list(value), start=1):
        if not isinstance(item, dict):
            continue
        chart = dict(item)
        chart['id'] = str(chart.get('id') or f'chart-{idx}')
        charts.append(chart)
    return charts[:12]


def _section_content_hints(section: Dict[str, Any]) -> List[str]:
    hints = []
    for item in _as_list(section.get('key_takeaways')):
        text = _normalize_punctuation(str(item or '').strip())
        if text:
            hints.append(text)
    for subsection in _as_list(section.get('subsections')):
        if isinstance(subsection, dict):
            text = _strip_number_prefix(subsection.get('title', ''))
        else:
            text = str(subsection or '')
        text = _normalize_punctuation(text)
        if text and not _is_generic_title(text):
            hints.append(text)
        if len(hints) >= 4:
            break
    return _dedupe(hints)[:4]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _item_to_text(value: Any) -> str:
    if isinstance(value, dict):
        return ' '.join(str(v) for v in value.values() if isinstance(v, (str, int, float)) and str(v).strip())
    if isinstance(value, list):
        return ' '.join(_item_to_text(x) for x in value)
    return str(value or '')


def _field(item: Any, key: str, default: str = '') -> str:
    if isinstance(item, dict):
        return ' '.join(str(item.get(key) or default).split())
    return ' '.join(str(item or default).split())


def _summary_items(value: Any) -> List[str]:
    raw = [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else ([str(value).strip()] if str(value).strip() else [])
    if len(raw) <= 2 and raw and len(' '.join(raw)) > 450:
        raw = [s.strip() for s in re.split(r'(?<=[.!?])\s+', ' '.join(raw)) if len(s.strip()) > 20]
    return [_normalize_punctuation(x) for x in raw[:8]]


def _looks_like_internal_label(text: str) -> bool:
    cleaned = _normalize_punctuation(text).strip().lower()
    labels = {
        'executive summary',
        'key findings',
        'management action plan',
        'risk register',
        'ceo decision scenario',
        'method and team',
        'methodology',
    }
    return cleaned in labels or cleaned.startswith(('ceo decision', 'management action', 'risk register'))


def _strip_number_prefix(text: str) -> str:
    return re.sub(r'^\s*\d+[\.)、]\s*', '', str(text or '')).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = ' '.join(str(value or '').replace('\n', ' ').split())
    text = _normalize_punctuation(text)
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(' ', 1)[0].strip()
    if not shortened:
        shortened = text[:max_chars].strip()
    return _clean_sentence_fragment(shortened)


def _normalize_punctuation(text: str) -> str:
    text = unicodedata.normalize('NFKC', str(text or ''))
    translation = {
        0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x2032: "'", 0xFF07: "'",
        0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"', 0x2033: '"', 0xFF02: '"',
        0x2010: '-', 0x2011: '-', 0x2012: '-', 0x2013: '-', 0x2014: '-', 0x2212: '-',
        0x00A0: ' ', 0x202F: ' ', 0x3000: ' ', 0xFF0C: ',', 0xFF0E: '.', 0xFF1A: ':', 0xFF1B: ';', 0xFF08: '(', 0xFF09: ')',
    }
    text = ''.join(translation.get(ord(ch), ch) for ch in text)
    text = re.sub(r"([A-Za-z])\s+'\s+s\b", r"\1's", text)
    text = re.sub(r"\b([A-Za-z]+n)\s+'\s+t\b", r"\1't", text)
    text = re.sub(r"\s+", ' ', text).strip()
    return text


def _reader_clean(text: str) -> str:
    text = _normalize_punctuation(text)
    for pattern in PROCESS_LANGUAGE_PATTERNS:
        text = re.sub(pattern + r'\s*', '', text, flags=re.I)
    replacements = [
        (r'\bCEO decision scenario\b', 'Board choice under uncertainty'),
        (r'\bManagement action plan\b', 'Near-term management moves'),
        (r'\bRisk register\b', 'Risk implications'),
        (r'\bMethod and team\b', 'About the research'),
        (r'\bKey findings\b', 'Main conclusions'),
        (r'\bExecutive summary\b', 'Opening view'),
        (r'\bEvidence boundary:\s*', 'The public record shows that '),
        (r'\bEvidence:\s*', ''),
        (r'\bManagement implication:\s*', ''),
        (r'\bThe management implication is clear:\s*', ''),
        (r'\bThe next management move is clear:\s*', ''),
        (r'\bThis should remain a directional conclusion because\s*', 'The available record supports a directional view because '),
        (r'\bThis point should be validated further because\s*', 'Leadership should validate whether '),
        (r'\bThis assumption should stay on the watchlist because\s*', 'This assumption needs continued scrutiny because '),
        (r'\bThe diligence gap is that\s*', 'The unresolved commercial question is whether '),
        (r'\bCEO question:\s*', 'Decision question: '),
        (r'\bRecommended move:\s*', 'Recommended move: '),
        (r'\bpublic-evidence boundary\b', 'available public record'),
        (r'\bevidence-boundary\b', 'available public record'),
        (r'\bevidence boundary\b', 'available public record'),
        (r'\bsource backup\b', 'supporting sources'),
        (r'\bevidence gates\b', 'verified milestones'),
        (r'\bdecision gates\b', 'decision milestones'),
        (r'\bvalidation gaps\b', 'open questions'),
        (r'\bmodel-assisted synthesis\b', 'research synthesis'),
        (r'\binternal executive strategy stress test\b', ''),
        (r'\binternal framework\b', ''),
        (r'\bstress test\b', 'review'),
        (r'\bavailable public record:\s*', 'The public record shows that '),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:1].upper() + text[1:] if text[:1].islower() else text


def _tex(value: Any) -> str:
    text = str(value or '').replace('\u00ad', '').replace('\ufffe', '').replace('\ufeff', '')
    text = _reader_clean(' '.join(text.replace('\n', ' ').split()))
    mapping = {'\\': r'\textbackslash{}', "'": r'\BOApos{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)
