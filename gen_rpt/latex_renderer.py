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
\defaultfontfeatures{Ligatures=NoCommon}
\IfFontExistsTF{Noto Sans CJK SC}{\setmainfont{Noto Sans CJK SC}\setsansfont{Noto Sans CJK SC}}{\setmainfont{DejaVu Sans}\setsansfont{DejaVu Sans}}
\definecolor{BOBlue}{HTML}{0055A4}
\definecolor{BOBright}{HTML}{3273F6}
\definecolor{BONavy}{HTML}{051C2C}
\definecolor{BOMuted}{HTML}{6F7F8F}
\definecolor{BOLine}{HTML}{DCE3EA}
\definecolor{BOLight}{HTML}{F4F8FC}
\setlength{\parindent}{0pt}
\setlength{\parskip}{4.2pt}
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
    title_text = str(report.get('report_title') or topic)
    title = _tex(title_text)
    summary = _summary_items(report.get('executive_summary', []))
    sections = _repair_sections(report, _safe_sections(report.get('sections', [])), topic, summary)
    refs = report.get('reference_institutions', []) or []
    parts = [HEADER, '\\begin{document}', '\\raggedright']
    parts.append(_cover_page(title, _asset_path(assets.get('cover-background', '')), topic))
    parts.append(_agenda_and_contents_page(summary, sections))
    parts.append(_executive_summary_page(report, summary))
    parts.append(_key_findings_page(report))
    parts.append(_action_plan_page(report))
    parts.append(_risk_register_page(report))
    parts.append(_scenario_page(report))
    parts.append(_methodology_page(report, refs))
    for idx, section in enumerate(sections, start=1):
        parts.append(_chapter_block(section, assets, idx))
    parts.append(_disclaimer_page(refs))
    parts.append('\\end{document}\n')
    return '\n'.join(parts)


def _cover_page(title: str, cover: str, topic: str) -> str:
    if cover:
        bg = '\\node[anchor=south west,inner sep=0] at (current page.south west) {\\includegraphics[width=\\paperwidth,height=\\paperheight]{' + cover + '}};\n\\fill[BONavy,opacity=.18] (current page.south west) rectangle (current page.north east);'
    else:
        bg = '\\fill[BONavy] (current page.south west) rectangle (current page.north east);'
    prepared = _tex('Prepared by BlueOcean | ' + date.today().isoformat())
    topic_line = _tex(_shorten(topic, 180))
    return r'''
\thispagestyle{empty}
\begin{tikzpicture}[remember picture,overlay]
''' + bg + r'''
\fill[white,opacity=.96] ([xshift=17mm,yshift=-28mm]current page.north west) rectangle ++(150mm,-62mm);
\fill[BOBright] ([xshift=17mm,yshift=-28mm]current page.north west) rectangle ++(150mm,-2.1mm);
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-37mm]current page.north west) {\sffamily\scriptsize\bfseries\color{BOBlue} BLUEOCEAN\\DEEP RESEARCH REPORT};
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-53mm]current page.north west) {\parbox{132mm}{\raggedright\sffamily\fontsize{22}{25}\selectfont\color{BONavy} ''' + title + r'''}};
\node[anchor=north west,text width=132mm] at ([xshift=25mm,yshift=-78mm]current page.north west) {\sffamily\scriptsize\color{BOMuted} ''' + prepared + r'''\\''' + topic_line + r'''};
\end{tikzpicture}
\clearpage
'''


def _agenda_and_contents_page(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    heading = _agenda_heading(summary, sections)
    summary_rows = []
    for idx, item in enumerate(summary[:5], start=1):
        summary_rows.append('\\textcolor{BOBlue}{\\bfseries ' + f'{idx:02d}' + '} & {\\footnotesize ' + _tex(_shorten(item, 245)) + '} \\\\[4pt]\n')
    if not summary_rows:
        summary_rows.append('\\textcolor{BOBlue}{\\bfseries 01} & {\\footnotesize Leadership should align resources to the few facts that change strategic choices.} \\\\[4pt]\n')

    content_rows = []
    for idx, section in enumerate(sections, start=1):
        title = _tex(_strip_number_prefix(section.get('title', 'Section')))
        content_rows.append('\\textcolor{BOBlue}{\\bfseries ' + str(idx) + '} & ' + title + ' & {\\color{BOMuted}pp.~\\pageref{chap:' + str(idx) + '}--\\pageref{chap:' + str(idx) + ':end}} \\\\[3pt]\n')

    return _kicker('Executive conclusions and contents') + _heading(heading) + _rule() + '\\begin{tabularx}{\\linewidth}{p{12mm}Y}\n' + ''.join(summary_rows) + '\\end{tabularx}\n\\vspace{6pt}\n{\\textcolor{BOBlue}{\\scriptsize\\bfseries CONTENTS}}\\par\\vspace{2pt}\n\\begin{tabularx}{\\linewidth}{p{8mm}Yp{29mm}}\n' + ''.join(content_rows) + '\\end{tabularx}\n\\clearpage\n'


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
    visual = _image_block(_resolve_image(section, assets, idx), '64mm', '45mm')
    chart_path = _resolve_chart(assets, idx)
    chart = _center_image(chart_path, '0.88\\linewidth', '62mm') if chart_path else ''

    chapter = '\\clearpage\n\\label{chap:' + str(idx) + '}\n' + _kicker('Chapter ' + str(idx)) + _heading(title)
    if lead and _normalize_punctuation(lead_raw).lower() != _normalize_punctuation(title_raw).lower():
        chapter += '{\\textcolor{BOBlue}{\\normalsize ' + lead + '}}\\par\\vspace{3pt}\n'
    chapter += '\\begin{minipage}[t]{0.58\\linewidth}\n' + _para(paras[0]) + _para(paras[1]) + _para(paras[2]) + '\\end{minipage}\\hfill\\begin{minipage}[t]{0.36\\linewidth}\n\\vspace{0pt}\n' + visual + '\n\\end{minipage}\\par\\vspace{5pt}\n'
    for paragraph in paras[3:6]:
        chapter += _para(paragraph)
    if chart:
        chapter += '\\vspace{5pt}\n' + chart + '\\vspace{4pt}\n'
    chapter += _subsection_blocks(section, paras[6:])
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
        return _tex(_shorten(cleaned, 118))
    if sections:
        return _tex(_shorten(_strip_number_prefix(sections[0].get('title', 'Management agenda')), 118))
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


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _field(item: Any, key: str, default: str = '') -> str:
    if isinstance(item, dict):
        return ' '.join(str(item.get(key) or default).split())
    return ' '.join(str(item or default).split())


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


def _tex(value: Any) -> str:
    text = str(value or '').replace('\u00ad', '').replace('\ufffe', '').replace('\ufeff', '')
    text = _normalize_punctuation(' '.join(text.replace('\n', ' ').split()))
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)
