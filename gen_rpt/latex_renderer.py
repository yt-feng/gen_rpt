from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

HEADER = r'''
\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=14mm,top=13mm,bottom=14mm]{geometry}
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
\hyphenpenalty=9000
\exhyphenpenalty=9000
\emergencystretch=2em
\sloppy
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\scriptsize\color{BOMuted} BlueOcean}
\fancyhead[R]{\scriptsize\color{BOMuted} Deep Research Report \hspace{6pt} \thepage}
\fancyfoot[L]{\scriptsize\color{BOMuted} BlueOcean}
\fancyfoot[C]{\scriptsize\color{BOMuted} This document is intended for strategy discussion.}
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
    parts.append(_agenda_and_contents_page(summary, sections))
    parts.append(_opening_page(report, summary, sections, assets, topic))
    for idx, section in enumerate(sections, start=1):
        parts.append(_chapter_block(section, assets, idx))
        if idx <= len(charts):
            parts.append(_exhibit_page(charts[idx - 1], assets, idx))
        if idx == 2:
            parts.append(_decision_story_page(report))
    parts.append(_leadership_agenda_page(report, sections))
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
\fill[white,opacity=.96] ([xshift=16mm,yshift=-27mm]current page.north west) rectangle ++(126mm,-82mm);
\fill[BOGreen] ([xshift=16mm,yshift=-27mm]current page.north west) rectangle ++(126mm,-2mm);
\node[anchor=north west,text width=108mm] at ([xshift=24mm,yshift=-37mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOMuted} BLUEOCEAN RESEARCH};
\node[anchor=north west,text width=108mm] at ([xshift=24mm,yshift=-51mm]current page.north west) {\parbox{108mm}{\raggedright\sffamily\fontsize{23}{27}\selectfont\color{BONavy} ''' + title + r'''}};
\node[anchor=north west,text width=108mm] at ([xshift=24mm,yshift=-94mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOText} ''' + topic_line + r'''\\\vspace{2pt}\color{BOMuted}''' + prepared + r'''};
\node[anchor=south east] at ([xshift=-10mm,yshift=8mm]current page.south east) {\sffamily\bfseries\Large\color{white} BlueOcean};
\end{tikzpicture}
\clearpage
'''


def _agenda_and_contents_page(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    rows = []
    rows.append('\\textcolor{BOGreen}{\\bfseries 03} & ' + _tex(_shorten(_agenda_heading(summary, sections), 95)) + ' \\\\[7pt]\n')
    start_page = 4
    for idx, section in enumerate(sections, start=1):
        title = _tex(_shorten(_strip_number_prefix(section.get('title', 'Section')), 100))
        page_no = f'{start_page + (idx - 1) * 2:02d}'
        rows.append('\\textcolor{BOGreen}{\\bfseries ' + page_no + '} & ' + title + ' \\\\[5pt]\n')
        for sub in _section_content_hints(section)[:3]:
            rows.append(' & {\\scriptsize\\color{BOMuted} ' + _tex(_shorten(sub, 72)) + '} \\\\[1pt]\n')
    rows.append('\\textcolor{BOGreen}{\\bfseries ' + f'{start_page + len(sections) * 2 + 1:02d}' + '} & ' + _tex('Future action agenda') + ' \\\\[5pt]\n')
    rows.append('\\textcolor{BOGreen}{\\bfseries ' + f'{start_page + len(sections) * 2 + 2:02d}' + '} & ' + _tex('About this research') + ' \\\\[5pt]\n')
    return (
        '\\clearpage\n'
        '{\\sffamily\\fontsize{26}{31}\\selectfont\\color{BONavy} Contents}\\par\\vspace{10pt}\n'
        '\\begin{tabularx}{\\linewidth}{p{14mm}Y}\n'
        + ''.join(rows)
        + '\\end{tabularx}\n\\clearpage\n'
    )


def _opening_page(report: Dict[str, Any], summary: List[str], sections: List[Dict[str, Any]], assets: Dict[str, str], topic: str) -> str:
    title = _tex(_shorten(_agenda_heading(summary, sections), 92))
    narrative = _normalize_punctuation(str(report.get('executive_summary_text') or '').strip())
    if not narrative:
        narrative = ' '.join(summary[:3])
    narrative = _tex(_shorten(narrative, 1200))
    visual = _asset_path(assets.get('image-1', '')) or _asset_path(assets.get('cover-background', ''))
    image = _full_width_image(visual, '0.47\\paperwidth', '39mm') if visual else ''
    bullets = summary[:4] or [_strip_number_prefix(x.get('title', '')) for x in sections[:4]]
    bullet_block = ''.join('\\item ' + _tex(_shorten(item, 145)) + '\n' for item in bullets if str(item).strip())
    if bullet_block:
        bullet_block = '\\begin{itemize}\n' + bullet_block + '\\end{itemize}\n'
    return (
        '\\clearpage\n'
        + (image + '\\vspace{6pt}\n' if image else '')
        + '{\\sffamily\\fontsize{22}{27}\\selectfont\\color{BONavy} ' + title + '}\\par\\vspace{4pt}\n'
        + '{\\sffamily\\fontsize{12}{16}\\selectfont\\color{BOGreen} ' + _tex(_shorten(topic, 150)) + '}\\par\\vspace{8pt}\n'
        + '\\begin{minipage}[t]{0.48\\linewidth}\n'
        + '{\\small\\color{BOText} ' + narrative + '}\\par\n'
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.45\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries WHAT CHANGES FOR LEADERS}}\\par\\vspace{3pt}\n'
        + '{\\footnotesize ' + bullet_block + '}'
        + '\\end{minipage}\n\\clearpage\n'
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
    lead = _tex(_shorten(lead_raw, 320))
    paras = _paras(section)
    visual_path = _resolve_image(section, assets, idx)
    visual = _full_width_image(visual_path, '\\linewidth', '43mm') if visual_path else ''

    chapter = '\\clearpage\n\\label{chap:' + str(idx) + '}\n'
    if idx % 2 == 1 and visual:
        chapter += visual + '\\vspace{5pt}\n'
    chapter += _kicker('Chapter ' + str(idx)) + _heading(title)
    if lead and _normalize_punctuation(lead_raw).lower() != _normalize_punctuation(title_raw).lower():
        chapter += '{\\sffamily\\fontsize{12}{15}\\selectfont\\color{BOGreen} ' + lead + '}\\par\\vspace{6pt}\n'
    if idx % 2 == 0:
        chapter += (
            '\\begin{minipage}[t]{0.55\\linewidth}\n'
            + _paragraph_group(paras[:4])
            + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.39\\linewidth}\n\\vspace{0pt}\n'
            + (visual if visual else _callout_box(_section_content_hints(section)[:3]))
            + '\\end{minipage}\\par\\vspace{4pt}\n'
        )
        chapter += _paragraph_group(paras[4:8])
    else:
        chapter += '\\begin{multicols}{2}\n' + _paragraph_group(paras[:8]) + '\\end{multicols}\n'
    chapter += _subsection_blocks(section, paras[8:])
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


def _paragraph_group(paragraphs: List[str]) -> str:
    return ''.join(_para(p) for p in paragraphs if str(p).strip())


def _callout_box(items: List[str]) -> str:
    bullets = ''.join('\\item ' + _tex(_shorten(item, 130)) + '\n' for item in items if str(item).strip())
    if not bullets:
        bullets = '\\item Focus on the few facts that change the management decision.\n'
    return (
        '\\fcolorbox{BOLine}{BOLight}{\\begin{minipage}[t][44mm][t]{0.95\\linewidth}'
        '\\vspace{4pt}{\\textcolor{BOGreen}{\\scriptsize\\bfseries WHAT TO WATCH}}\\par'
        '\\vspace{2pt}{\\footnotesize\\begin{itemize}\n' + bullets + '\\end{itemize}}'
        '\\end{minipage}}'
    )


def _exhibit_page(chart: Dict[str, Any], assets: Dict[str, str], idx: int) -> str:
    title = _tex(_shorten(chart.get('title') or f'Figure {idx}', 125))
    subtitle = _tex(_shorten(chart.get('subtitle') or chart.get('caption') or '', 210))
    caption = _tex(_shorten(chart.get('caption') or '', 260))
    source = _tex(_shorten(chart.get('source_note') or 'Source: public sources and BlueOcean synthesis.', 180))
    path = _asset_path(assets.get(str(chart.get('id') or f'chart-{idx}'), '')) or _resolve_chart(assets, idx)
    visual = _center_image(path, '0.95\\linewidth', '98mm') if path else _callout_box([caption or subtitle])
    return (
        '\\clearpage\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries FIGURE ' + str(idx) + '}}\\par\\vspace{3pt}\n'
        + '{\\sffamily\\fontsize{15}{19}\\selectfont\\color{BONavy} ' + title + '}\\par\n'
        + ('{\\small\\color{BOMuted} ' + subtitle + '}\\par\\vspace{6pt}\n' if subtitle else '\\vspace{6pt}\n')
        + visual
        + ('\\vspace{3pt}{\\footnotesize\\color{BOText} ' + caption + '}\\par\n' if caption else '')
        + '{\\scriptsize\\color{BOMuted} ' + source + '}\\par\n'
    )


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
        title = 'A boardroom question'
        situation = 'Leadership must decide which moves can be made now and which should wait for stronger evidence.'
        question = 'What should be done before the next major commitment?'
        move = 'Keep learning options open while reserving larger commitments for verified proof points.'
        watchouts = 'Avoid treating market enthusiasm as a substitute for validated economics.'
    if _looks_like_internal_label(title):
        title = 'A concrete executive choice'
    return (
        '\\clearpage\n'
        + '{\\sffamily\\fontsize{21}{26}\\selectfont\\color{BONavy} ' + _tex(_shorten(title, 100)) + '}\\par\\vspace{7pt}\n'
        + '\\begin{minipage}[t]{0.47\\linewidth}\n'
        + _para(_tex(_shorten(situation, 620)))
        + _para(_tex(_shorten(move, 520)))
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.45\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries DECISION QUESTION}}\\par\\vspace{3pt}\n'
        + '{\\sffamily\\fontsize{14}{18}\\selectfont\\color{BOText} ' + _tex(_shorten(question, 260)) + '}\\par\\vspace{8pt}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries WATCHOUT}}\\par\\vspace{3pt}\n'
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
        risk_items.append('; '.join(x for x in [risk, trigger, action] if x))
    left = ''.join('\\item ' + _tex(_shorten(x, 170)) + '\n' for x in action_items if str(x).strip())
    right = ''.join('\\item ' + _tex(_shorten(x, 170)) + '\n' for x in risk_items if str(x).strip())
    return (
        '\\clearpage\n'
        + '{\\sffamily\\fontsize{21}{26}\\selectfont\\color{BONavy} Future action agenda}\\par\\vspace{7pt}\n'
        + '\\begin{minipage}[t]{0.47\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries PRIORITIES}}\\par\\vspace{3pt}\n'
        + '{\\small\\begin{itemize}\n' + (left or '\\item Convert the report into a short list of decisions, owners and proof points.\n') + '\\end{itemize}}\n'
        + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.45\\linewidth}\n'
        + '{\\textcolor{BOGreen}{\\scriptsize\\bfseries SIGNALS TO WATCH}}\\par\\vspace{3pt}\n'
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
    return '\\clearpage\n{\\sffamily\\fontsize{20}{25}\\selectfont\\color{BONavy} About this research}\\par\\vspace{6pt}\n' + _rule() + '{\\footnotesize\\color{BOMuted} ' + body + '}\n'


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
        paragraphs = [str(x).strip() for x in section.get('paragraphs', []) if str(x).strip()]
        if _paragraphs_are_weak(paragraphs):
            paragraphs = _generated_paragraphs(idx, current_title, report_title, summary)
        elif len(paragraphs) < 10:
            paragraphs = _dedupe(paragraphs + _generated_paragraphs(idx, current_title, report_title, summary))[:10]
        else:
            paragraphs = _dedupe(paragraphs)
        section['paragraphs'] = [_normalize_punctuation(p) for p in paragraphs[:12]]
        repaired.append(section)
    return _merge_sections(repaired, max_sections=6, report_title=report_title, summary=summary)


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
        paragraphs = _dedupe(paragraphs + _generated_paragraphs(idx, merged_title, report_title, summary))[:14]
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
        cleaned = cleaned[:117].rsplit(' ', 1)[0].strip()
    return cleaned or 'The management agenda should be staged around evidence quality'


def _derive_lead(idx: int, title: str, report_title: str, summary: List[str]) -> str:
    if idx - 1 < len(summary):
        return _shorten(summary[idx - 1], 300)
    return 'The central question is how quickly the market can convert technical progress into bankable deployment evidence.'


def _generated_paragraphs(idx: int, title: str, report_title: str, summary: List[str]) -> List[str]:
    topic = _normalize_punctuation(report_title)
    thesis = _normalize_punctuation(summary[(idx - 1) % len(summary)]) if summary else title
    return [
        thesis,
        'The issue matters because ' + topic + ' is moving from a science-led narrative into a capital-allocation question. Executives need to know which milestones alter decision quality and which milestones merely improve market sentiment.',
        'The first lens is technology readiness. Progress in laboratories, pilots and private-company demonstrations should be separated from repeatable operating performance, because commercial buyers will ultimately underwrite uptime, maintainability, safety case, supply availability and lifecycle economics rather than headline breakthroughs.',
        'The second lens is economics. Even when technical performance improves, the commercial case depends on capital intensity, construction duration, financing cost, utilization, regulatory treatment and the availability of credible offtake or procurement pathways. These factors can widen or narrow the gap between promise and adoption.',
        'The third lens is ecosystem readiness. Suppliers, talent pools, standards bodies, regulators, insurers and customers all need to mature in parallel. A bottleneck in any one of these areas can delay deployment even if the core technology continues to advance.',
        'The implication is that leadership teams should avoid binary conclusions. A more robust posture is to treat the opportunity as a staged option: participate early enough to learn and secure access, but reserve heavy commitments for moments when the evidence base becomes more bankable.',
        'Partnerships will be especially important. Collaboration with technology developers, utilities, industrial customers, laboratories and public agencies can reduce learning cost while preserving strategic flexibility. The best partnerships are those that create observable proof points rather than broad memoranda of understanding.',
        'Capital should therefore be sequenced around milestones. The most useful milestones are not generic announcements; they are measurable changes in cost, performance, reliability, permitting clarity, supply-chain depth and customer willingness to sign binding commercial arrangements.',
        'For senior executives, the practical agenda is to identify the few assumptions that would change the decision. Those assumptions should be monitored through a dashboard, reviewed quarterly and linked to clear escalation triggers for partnerships, investment or market entry.',
        'The risk of moving too slowly is losing access to scarce partners and capabilities. The risk of moving too quickly is committing capital before the market has resolved its most material uncertainties. The strategic answer is disciplined optionality rather than passive observation.',
        'This chapter therefore frames the topic as a management choice, not a technology forecast. The objective is to define where conviction is already sufficient, where more evidence is required, and where a low-cost option should be maintained.',
        'The conclusion is that management should prioritize evidence quality, milestone discipline and partner access over headline excitement. Those levers are more likely to determine value creation than any single technical announcement.',
    ]


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
        return _shorten(cleaned, 118)
    if sections:
        return _shorten(_strip_number_prefix(sections[0].get('title', 'Management agenda')), 118)
    return 'Management should focus on the few moves that can change the outcome'


def _paras(section: Dict[str, Any]) -> List[str]:
    raw = [str(x) for x in section.get('paragraphs', []) if str(x).strip()]
    title = _strip_number_prefix(section.get('title', 'the issue'))
    expansions = _generated_paragraphs(1, title, title, [section.get('lead') or title])
    raw = _dedupe(raw)
    while len(raw) < 12:
        raw.append(expansions[(len(raw) - 1) % len(expansions)])
        raw = _dedupe(raw)
    return [_tex(x) for x in raw[:12]]


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
    return charts[:8]


def _section_content_hints(section: Dict[str, Any]) -> List[str]:
    hints = []
    for item in _as_list(section.get('key_takeaways')):
        text = _normalize_punctuation(str(item or '').strip())
        if text:
            hints.append(text)
    for item in _as_list(section.get('paragraphs')):
        text = _normalize_punctuation(str(item or '').strip())
        if text and len(text) > 35:
            hints.append(_title_from_sentence(text))
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
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + '.'


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
    replacements = [
        (r'\bCEO decision scenario\b', 'A concrete executive choice'),
        (r'\bManagement action plan\b', 'Future action agenda'),
        (r'\bRisk register\b', 'Signals to watch'),
        (r'\bMethod and team\b', 'About this research'),
        (r'\bKey findings\b', 'Main conclusions'),
        (r'\bExecutive summary\b', 'Opening view'),
        (r'\bEvidence:\s*', ''),
        (r'\bManagement implication:\s*', ''),
        (r'\bCEO question:\s*', 'Decision question: '),
        (r'\bRecommended move:\s*', 'Recommended move: '),
        (r'\bpublic-evidence boundary\b', 'available public record'),
        (r'\bevidence boundary\b', 'available public record'),
        (r'\bsource backup\b', 'supporting sources'),
        (r'\bevidence gates\b', 'verified milestones'),
        (r'\bdecision gates\b', 'decision milestones'),
        (r'\bvalidation gaps\b', 'open questions'),
        (r'\bmodel-assisted synthesis\b', 'research synthesis'),
        (r'\binternal executive strategy stress test\b', ''),
        (r'\binternal framework\b', ''),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r'\s+', ' ', text).strip()


def _tex(value: Any) -> str:
    text = str(value or '').replace('\u00ad', '').replace('\ufffe', '').replace('\ufeff', '')
    text = _reader_clean(' '.join(text.replace('\n', ' ').split()))
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)
