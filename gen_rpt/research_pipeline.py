from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .brand_assets import copy_or_generate_brand_assets, summarize_reference_institutions, write_reference_backup
from .deepseek_client import DeepSeekClient
from .graphics import create_chart, create_insight_card, ensure_dir
from .pdf_qa import apply_pdf_qa_fixes, run_pdf_qa
from .pdf_renderer import render_pdf_from_html
from .ppt_renderer import render_pptx
from .presentation_renderer import render_presentation_html
from .report_renderer import render_report_html, render_report_markdown
from .web_fetch import SourceDocument, collect_sources


class ResearchPipeline:
    def __init__(self, client: DeepSeekClient, language: str = "zh", target_length: int | None = None) -> None:
        self.client = client
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.target_length = target_length or (1500 if self.language == "en" else 3000)

    def build_report(self, topic: str, output_dir: Path) -> Dict:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)

        plan = self._plan_research(topic)
        queries = plan.get("search_queries", [])[:4]
        sources = collect_sources(queries, per_query=3, max_sources=8)
        source_dicts = [source.__dict__ for source in sources]

        report = self._synthesize_report(topic, plan, sources)
        report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), source_dicts)

        asset_map = copy_or_generate_brand_assets(assets_dir)
        asset_map.update(self._materialize_assets(report, assets_dir))
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)

        html_path, markdown_path, pdf_path = self._render_report_pack(report, asset_map, output_dir, topic)
        qa_dir = output_dir / "backup" / "qa"
        qa_result = run_pdf_qa(pdf_path, html_path, qa_dir)

        final_report = report
        if not qa_result.get("passed", False):
            final_report = apply_pdf_qa_fixes(report, qa_result)
            (output_dir / "report_payload_prefixed.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            html_path, markdown_path, pdf_path = self._render_report_pack(final_report, asset_map, output_dir, topic)
            qa_result = run_pdf_qa(pdf_path, html_path, qa_dir / "after_fix")

        pptx_path = render_pptx(final_report, asset_map, output_dir / "report.pptx", topic, self.language)
        presentation_path = render_presentation_html(final_report, asset_map, output_dir / "presentation.html", topic, self.language)

        (output_dir / "report_payload.json").write_text(
            json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "research_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "sources.json").write_text(
            json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "qa_result.json").write_text(
            json.dumps(qa_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "plan": plan,
            "sources": source_dicts,
            "report": final_report,
            "asset_map": asset_map,
            "output_dir": str(output_dir),
            "backup_dir": str(backup_dir),
            "language": self.language,
            "target_length": self.target_length,
            "html_path": str(html_path),
            "markdown_path": str(markdown_path),
            "pdf_path": str(pdf_path),
            "pptx_path": str(pptx_path),
            "presentation_path": str(presentation_path),
            "qa_result": qa_result,
        }

    def _render_report_pack(self, report: Dict, asset_map: Dict[str, str], output_dir: Path, topic: str):
        html_path = render_report_html(report=report, assets=asset_map, output_file=output_dir / "report.html", topic=topic, language=self.language)
        markdown_path = render_report_markdown(report=report, assets=asset_map, output_file=output_dir / "report.md", topic=topic, language=self.language)
        pdf_path = render_pdf_from_html(html_path, output_dir / "report.pdf")
        return html_path, markdown_path, pdf_path

    def _lang_instruction(self) -> str:
        return "Use English for the whole report." if self.language == "en" else "全程使用中文输出。"

    def _length_instruction(self) -> str:
        if self.language == "en":
            return f"Target around {self.target_length} words for the full report body and summary combined. Keep each section concise enough to fit one PDF page."
        return f"全文正文与摘要合计目标约 {self.target_length} 个中文字符；每个章节要短，适合一页 PDF 呈现。"

    def _title_style_instruction(self) -> str:
        if self.language == "en":
            return "Use pyramid-principle writing. Titles must be conclusion-first, crisp, sharp, and executive-ready: subject + active verb + implication. Avoid generic headings."
        return "遵循金字塔原理。标题必须是结论，不是标签；用“主体 + 动词 + 判断/影响”的结构，短促、锋利、可供高管快速判断。"

    def _method_schema_instruction(self) -> str:
        if self.language == "en":
            return "Add method_steps with exactly seven items: 1 Define the decision question; 2 Build the issue tree; 3 Prioritize drivers; 4 Gather evidence; 5 Isolate the crux; 6 Develop options; 7 Recommend actions. Also add issue_tree with 3-5 branches, each containing question, why_it_matters, and evidence_needed."
        return "新增 method_steps，正好 7 项，遵循七步法：1 定义决策问题；2 拆解 issue tree；3 排序关键驱动；4 收集证据；5 锁定问题要害；6 形成战略选项；7 给出行动建议。同时新增 issue_tree，包含 3-5 个分支，每个分支包含 question、why_it_matters、evidence_needed。"

    def _plan_research(self, topic: str) -> Dict:
        system = "You are a world-class deep research planner. Design a plan that follows both Deep Research and strategy-consulting problem-solving workflows. Return JSON only."
        if self.language == "en":
            user = f"""
Create a research plan for the following topic and return JSON only.

Topic: {topic}

Required JSON fields:
- objective
- audience
- decision_question: the single most important management question
- issue_tree: 3-5 branches, each with question, why_it_matters, evidence_needed
- search_queries: 4-6 public web search queries in English when possible
- outline: 4-6 conclusion-first section titles
- chart_ideas: 2-3 chart opportunities
- insight_card_ideas: 2-3 executive insight card ideas
- risks: data or evidence risks

Requirements:
- Use English
- Keep search queries search-engine friendly
- Outline titles must be conclusion-first and crisp
- Do not output markdown
"""
        else:
            user = f"""
为下面这个选题生成一份研究计划，输出 JSON：

选题：{topic}

JSON 字段要求：
- objective: 研究目标
- audience: 目标读者
- decision_question: 最核心的管理决策问题
- issue_tree: 3-5 个问题分支，每个包含 question、why_it_matters、evidence_needed
- search_queries: 4-6 个适合公开网络检索的查询语句
- outline: 4-6 个结论先行的章节标题
- chart_ideas: 2-3 个适合图表化的角度
- insight_card_ideas: 2-3 个适合做高管洞察图卡的角度
- risks: 研究中可能出现的数据风险或口径风险

要求：
- 默认使用中文
- 查询语句尽量适合搜索引擎
- outline 标题必须是结论，不要写成标签
- 不要输出 markdown，只输出 JSON
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument]) -> Dict:
        source_blocks = []
        for idx, src in enumerate(sources, start=1):
            excerpt = src.content[:2500]
            source_blocks.append(f"[Source {idx}]\nTitle: {src.title}\nURL: {src.url}\nSearch Query: {src.query}\nSnippet: {src.snippet}\nExcerpt:\n{excerpt}")

        source_text = "\n\n".join(source_blocks)
        if not source_text:
            source_text = "Insufficient web evidence was fetched. Build a clear analysis framework and explicitly mark where more evidence is needed." if self.language == "en" else "暂无抓取到足够网页资料，请基于选题输出可执行的分析框架，并明确指出需要后续补充外部证据。"

        system = "You are an elite strategy consultant and research writer. Use only the provided source material as factual grounding. Return strict JSON only."
        if self.language == "en":
            user = f"""
Generate a BlueOcean-branded research report data structure and return JSON only.

Topic:
{topic}

Language rule:
{self._lang_instruction()}

Length rule:
{self._length_instruction()}

Headline rule:
{self._title_style_instruction()}

Problem-solving rule:
{self._method_schema_instruction()}

Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Sources:
{source_text}

Required JSON fields:
- report_title: conclusion-first title, no more than 18 words
- report_subtitle: scope and audience in one sentence
- executive_summary: 6-9 concise highlight bullets; each starts with the core implication or number
- method_steps: exactly 7 items with name and description
- issue_tree: 3-5 branches with question, why_it_matters, evidence_needed
- sections: 4-6 items, each contains id, title, lead, paragraphs, key_takeaways, visual_hint
- insight_cards: 2-3 items, each contains id, title, subtitle, bullets, highlight_number, highlight_label, exhibit_label
- charts: 2-4 items, each contains id, exhibit_no, title, subtitle, type, categories, series, x_label, y_label, caption, source_note
- references: array with title, url, note

Hard requirements:
1. Use pyramid structure: answer first, evidence second, implication third.
2. Use seven-step problem solving and issue-tree logic before recommendations.
3. Apply 10 Tests thinking where relevant: market beating, advantage, where-to-play granularity, trend timing, privileged insight, uncertainty, commitment/flexibility, bias, conviction, and actionability.
4. Keep visuals BlueOcean memo style: blue emphasis, gray comparison, thin lines, no decorative UI cards.
5. Prefer horizontal ranking bar charts, comparison matrices, or scenario tables over generic pies.
6. Avoid text overflow: visual titles <= 12 words, card bullets <= 12 words, category labels compact.
7. Do not include meta labels such as BCG-style or McKinsey-style in final visible content.
8. references may only use real URLs from source materials.
9. Output JSON only.
"""
        else:
            user = f"""
请生成一份 BlueOcean 品牌化的研究报告数据结构，输出 JSON。

选题：
{topic}

语言要求：
{self._lang_instruction()}

篇幅要求：
{self._length_instruction()}

标题要求：
{self._title_style_instruction()}

问题拆解要求：
{self._method_schema_instruction()}

研究计划：
{json.dumps(plan, ensure_ascii=False, indent=2)}

资料：
{source_text}

JSON 字段要求：
- report_title: 结论先行的报告标题，不超过 28 个中文字符
- report_subtitle: 一句话说明范围和读者
- executive_summary: 6-9 条高亮结论，每条以判断、数字或影响开头
- method_steps: 正好 7 项，每项包含 name 和 description
- issue_tree: 3-5 个分支，每个包含 question、why_it_matters、evidence_needed
- sections: 4-6 项，每项包含 id、title、lead、paragraphs、key_takeaways、visual_hint
- insight_cards: 2-3 项，每项包含 id、title、subtitle、bullets、highlight_number、highlight_label、exhibit_label
- charts: 2-4 项，每项包含 id、exhibit_no、title、subtitle、type、categories、series、x_label、y_label、caption、source_note
- references: 数组，每项包含 title、url、note

硬性要求：
1. 遵循金字塔结构：先答案，再证据，再影响。
2. 使用七步法和 issue tree 逻辑，先拆问题，再给判断和行动。
3. 融合战略十问思维：能否胜出、优势来源、在哪里竞争、趋势、独到洞见、不确定性、承诺与灵活性、偏见、执行决心、行动化。
4. 图表遵循 BlueOcean memo 风格：蓝色强调、灰色对比、薄线条、白底、避免装饰性 UI 卡片。
5. 优先使用横向排名条形图、对比矩阵、场景表；少用普通饼图。
6. 防止文字溢出：图表标题短，图卡 bullet 不超过 18 个中文字符，类目标签短。
7. 最终可见内容不要出现“BCG-style”“McKinsey-style”“样例图卡”“制作说明”等元描述。
8. references 只允许使用资料区真实出现过的 URL。
9. 不要输出 markdown，只输出 JSON。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.22)

    def _materialize_assets(self, report: Dict, assets_dir: Path) -> Dict[str, str]:
        asset_map: Dict[str, str] = {}
        for card in report.get("insight_cards", []):
            target = assets_dir / f"{card['id']}.png"
            create_insight_card(card, target)
            asset_map[card["id"]] = f"assets/{target.name}"

        for chart in report.get("charts", []):
            target = assets_dir / f"{chart['id']}.png"
            create_chart(chart, target)
            asset_map[chart["id"]] = f"assets/{target.name}"

        return asset_map
