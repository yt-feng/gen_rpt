from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .brand_assets import copy_or_generate_brand_assets, summarize_reference_institutions, write_reference_backup
from .deepseek_client import DeepSeekClient
from .graphics import create_chart, create_insight_card, ensure_dir
from .image_generator import generate_ai_image_assets
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
        # target_length is kept for backward compatibility, but no longer used as a hard cap.
        self.target_length = target_length or 0

    def build_report(self, topic: str, output_dir: Path) -> Dict:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)

        plan = self._plan_research(topic)
        queries = plan.get("search_queries", [])[:6]
        sources = collect_sources(queries, per_query=3, max_sources=14)
        source_dicts = [source.__dict__ for source in sources]

        report = self._synthesize_report(topic, plan, sources)
        report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), source_dicts)
        self._ensure_visual_hints(report)

        asset_map = copy_or_generate_brand_assets(assets_dir)
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)
        asset_map.update(generate_ai_image_assets(self.client, topic, report, assets_dir, Path(backup_dir), language=self.language))
        asset_map.update(self._materialize_assets(report, assets_dir))

        html_path, markdown_path, pdf_path = self._render_report_pack(report, asset_map, output_dir, topic)
        qa_dir = output_dir / "backup" / "qa"
        qa_result = run_pdf_qa(pdf_path, html_path, qa_dir)

        final_report = report
        if not qa_result.get("passed", False):
            final_report = apply_pdf_qa_fixes(report, qa_result)
            self._ensure_visual_hints(final_report)
            (output_dir / "report_payload_prefixed.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            html_path, markdown_path, pdf_path = self._render_report_pack(final_report, asset_map, output_dir, topic)
            qa_result = run_pdf_qa(pdf_path, html_path, qa_dir / "after_fix")

        pptx_path = render_pptx(final_report, asset_map, output_dir / "report.pptx", topic, self.language)
        presentation_path = render_presentation_html(final_report, asset_map, output_dir / "presentation.html", topic, self.language)

        (output_dir / "report_payload.json").write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "qa_result.json").write_text(json.dumps(qa_result, ensure_ascii=False, indent=2), encoding="utf-8")

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

    def _scope_instruction(self) -> str:
        if self.language == "en":
            return "Do not target a fixed word count. Produce a client-ready research report that naturally renders to roughly 10-30 PDF pages, depending on evidence depth. Avoid padding, but do not truncate analysis."
        return "不要按固定字数写作。目标是一份可直接分发给客户的研究报告，最终自然渲染为约 10-30 页 PDF；不要灌水，但也不要为了控页数截断分析。"

    def _title_style_instruction(self) -> str:
        if self.language == "en":
            return "Use pyramid-principle writing. Titles must be conclusion-first, crisp, sharp, and executive-ready: subject + active verb + implication. Avoid generic headings."
        return "遵循金字塔原理。标题必须是结论，不是标签；用“主体 + 动词 + 判断/影响”的结构，短促、锋利、可供高管快速判断。"

    def _method_instruction(self) -> str:
        if self.language == "en":
            return "Use seven-step problem solving, issue trees, and 10 Tests as internal writing discipline only. Do not create a visible methodology or approach page unless it directly supports the client recommendation."
        return "把七步法、issue tree、战略十问作为内部写作心法融入分析，不要在正式报告里单独写成 Approach 或方法论页面，除非它直接服务于客户结论。"

    def _plan_research(self, topic: str) -> Dict:
        system = "You are a world-class deep research planner. Design a plan that follows deep research plus strategy-consulting problem solving. Return JSON only."
        if self.language == "en":
            user = f"""
Create a research plan for the following topic and return JSON only.

Topic: {topic}

Required JSON fields:
- objective
- audience
- decision_question
- issue_tree: 4-7 branches, each with question, why_it_matters, evidence_needed
- search_queries: 6-8 public web search queries
- outline: 6-10 conclusion-first section titles
- chart_ideas: 4-8 chart opportunities; prioritize market sizing, segmentation, share, adoption curve, regional heatmap, value chain, competitor positioning, scenario comparison
- insight_card_ideas: 2-4 executive insight card ideas
- risks: data or evidence risks

Requirements:
- Use English
- Keep search queries search-engine friendly
- Outline titles must be conclusion-first and crisp
- Do not output markdown
"""
        else:
            user = f"""
为下面这个选题生成研究计划，输出 JSON：

选题：{topic}

JSON 字段要求：
- objective: 研究目标
- audience: 目标读者
- decision_question: 最核心的管理决策问题
- issue_tree: 4-7 个问题分支，每个包含 question、why_it_matters、evidence_needed
- search_queries: 6-8 个适合公开网络检索的查询语句
- outline: 6-10 个结论先行的章节标题
- chart_ideas: 4-8 个图表机会，优先包括市场规模、细分结构、份额、渗透率曲线、区域热力、价值链、竞争定位、情景比较
- insight_card_ideas: 2-4 个高管洞察图卡
- risks: 数据风险或口径风险

要求：
- 默认使用中文
- 查询语句适合搜索引擎
- outline 标题必须是结论，不要写成标签
- 不要输出 markdown，只输出 JSON
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument]) -> Dict:
        source_blocks = []
        for idx, src in enumerate(sources, start=1):
            excerpt = src.content[:3200]
            source_blocks.append(f"[Source {idx}]\nTitle: {src.title}\nURL: {src.url}\nSearch Query: {src.query}\nSnippet: {src.snippet}\nExcerpt:\n{excerpt}")

        source_text = "\n\n".join(source_blocks)
        if not source_text:
            source_text = "Insufficient web evidence was fetched. Build a clear analysis framework and explicitly mark where more evidence is needed." if self.language == "en" else "暂无抓取到足够网页资料，请基于选题输出可执行的分析框架，并明确指出需要后续补充外部证据。"

        system = "You are an elite strategy consultant and research writer. Use only the provided source material as factual grounding. Return strict JSON only."
        if self.language == "en":
            user = f"""
Generate a client-ready BlueOcean research report data structure and return JSON only.

Topic:
{topic}

Language rule:
{self._lang_instruction()}

Scope rule:
{self._scope_instruction()}

Headline rule:
{self._title_style_instruction()}

Method rule:
{self._method_instruction()}

Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Sources:
{source_text}

Required JSON fields:
- report_title: conclusion-first title, no more than 22 words
- report_subtitle: scope and audience in one sentence
- executive_summary: 6-8 concise highlight bullets
- method_steps: exactly 7 items with name and description for backup only; do not make them visible as a report section
- issue_tree: 4-7 branches with question, why_it_matters, evidence_needed
- sections: 7-12 items, each contains id, title, lead, paragraphs, key_takeaways, visual_hint
- insight_cards: 2-4 items, each contains id, title, subtitle, bullets, highlight_number, highlight_label, exhibit_label
- charts: 4-8 items, each contains id, exhibit_no, title, subtitle, type, categories, series, x_label, y_label, caption, source_note
- references: array with title, url, note

Hard requirements:
1. Client-ready output: do not expose methodology pages, scratchpad, or meta labels.
2. Use pyramid structure: answer first, evidence second, implication third.
3. Each section should have 4-7 coherent paragraphs; no paragraph may end with ellipses.
4. At least half of sections should set visual_hint to a chart id such as chart-1, chart-2, etc.; remaining sections may use image ids only if visuals add value.
5. Charts must be analytically useful exhibits, not decoration. Prefer horizontal ranking bars, segment mix, adoption curve, region comparison, competitor position, value chain, and scenarios.
6. If data is approximate or synthesized from sources, state so in caption or source_note.
7. Avoid text overflow but never truncate with ellipses.
8. references may only use real URLs from source materials.
9. Output JSON only.
"""
        else:
            user = f"""
请生成一份 client-ready、可直接分发的 BlueOcean 研究报告数据结构，输出 JSON。

选题：
{topic}

语言要求：
{self._lang_instruction()}

篇幅要求：
{self._scope_instruction()}

标题要求：
{self._title_style_instruction()}

方法要求：
{self._method_instruction()}

研究计划：
{json.dumps(plan, ensure_ascii=False, indent=2)}

资料：
{source_text}

JSON 字段要求：
- report_title: 结论先行的报告标题，不超过 32 个中文字符
- report_subtitle: 一句话说明范围和读者
- executive_summary: 6-8 条高亮结论
- method_steps: 正好 7 项，每项包含 name 和 description，仅用于备份，不要在正式文件里单独展示
- issue_tree: 4-7 个分支，每个包含 question、why_it_matters、evidence_needed
- sections: 7-12 项，每项包含 id、title、lead、paragraphs、key_takeaways、visual_hint
- insight_cards: 2-4 项，每项包含 id、title、subtitle、bullets、highlight_number、highlight_label、exhibit_label
- charts: 4-8 项，每项包含 id、exhibit_no、title、subtitle、type、categories、series、x_label、y_label、caption、source_note
- references: 数组，每项包含 title、url、note

硬性要求：
1. 正式文件必须 client-ready，不要暴露 Approach、方法论、scratchpad 或元描述。
2. 遵循金字塔结构：先答案，再证据，再影响。
3. 每个 section 写 4-7 段连贯正文；任何段落或标题都不要以省略号结尾。
4. 至少一半 section 的 visual_hint 指向 chart-1、chart-2 等图表；其他 section 只有在图片确实有帮助时才指向 image。
5. 图表必须是分析型 exhibit，不是装饰图。优先用横向排名、结构拆分、渗透率曲线、区域对比、竞争定位、价值链、情景比较。
6. 图表数据若为示意性整理，必须在 caption 或 source_note 中说明。
7. 避免溢出，但不要用省略号截断文本。
8. references 只允许使用资料区真实出现过的 URL。
9. 不要输出 markdown，只输出 JSON。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.22)

    def _ensure_visual_hints(self, report: Dict) -> None:
        charts = report.get("charts", []) or []
        if not charts:
            return
        chart_ids = [c.get("id", f"chart-{idx}") for idx, c in enumerate(charts, start=1)]
        for idx, section in enumerate(report.get("sections", [])):
            hint = str(section.get("visual_hint", ""))
            if idx % 2 == 0 or not hint:
                section["visual_hint"] = chart_ids[idx % len(chart_ids)]

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
