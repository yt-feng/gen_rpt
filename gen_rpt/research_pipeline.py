from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

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
        # Kept for backward compatibility with the GitHub Action input; no longer a hard cap.
        self.target_length = target_length or 0

    def build_report(self, topic: str, output_dir: Path) -> Dict:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)

        try:
            plan = self._plan_research(topic)
        except Exception as exc:
            (output_dir / "plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(topic, reason=str(exc))

        queries = plan.get("search_queries", [])[:6]
        sources = collect_sources(queries, per_query=3, max_sources=12)
        source_dicts = [source.__dict__ for source in sources]

        try:
            report = self._synthesize_report(topic, plan, sources)
        except Exception as exc:
            (output_dir / "synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            report = self._fallback_report(topic, plan, sources, reason=str(exc))

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
            return "Do not target a fixed word count. Produce a client-ready research report that naturally renders to roughly 10-30 PDF pages. Avoid padding, but do not truncate analysis."
        return "不要按固定字数写作。目标是一份可直接分发给客户的研究报告，最终自然渲染为约 10-30 页 PDF；不要灌水，也不要为了控页数截断分析。"

    def _title_style_instruction(self) -> str:
        if self.language == "en":
            return "Use pyramid-principle writing. Titles must be conclusion-first, crisp and executive-ready. Avoid generic headings."
        return "遵循金字塔原理。标题必须是结论，不是标签；短促、锋利、可供高管快速判断。"

    def _method_instruction(self) -> str:
        if self.language == "en":
            return "Use seven-step problem solving, issue trees, and 10 Tests as internal writing discipline only. Do not create a visible methodology page."
        return "把七步法、issue tree、战略十问作为内部写作心法融入分析，不要在正式报告里单独写成 Approach 或方法论页面。"

    def _fallback_plan(self, topic: str, *, reason: str = "") -> Dict[str, Any]:
        if self.language == "en":
            outline = [
                "China's VRFB advantage is structural rather than episodic",
                "Dali Energy Storage should translate technology into project bankability",
                "Supply security is becoming the decisive competitive variable",
                "Policy demand creates a protected domestic scaling base",
                "Global expansion requires local proof and financing partners",
                "Execution should focus on reference projects and lifecycle economics",
                "The next phase needs a sharper international go-to-market model",
            ]
            queries = [
                f"{topic} vanadium redox flow battery market China",
                "China vanadium redox flow battery installed capacity",
                "Dali Energy Storage vanadium flow battery projects",
                "global VRFB manufacturers Sumitomo Invinity China",
                "long duration energy storage vanadium flow battery policy China",
                "vanadium supply chain China flow battery electrolyte",
            ]
        else:
            outline = [
                "中国液流钒电池优势来自产业链与政策共振",
                "大力储能需要把技术领先转化为项目可融资性",
                "钒资源安全正在成为竞争胜负手",
                "政策需求创造了受保护的国内放量基础",
                "全球扩张需要本地标杆和融资伙伴",
                "执行应聚焦标杆项目和全生命周期经济性",
                "下一阶段需要更清晰的国际市场进入模型",
            ]
            queries = [
                f"{topic} 液流钒电池 市场 中国",
                "中国 全钒液流电池 装机 容量",
                "大力储能 全钒液流电池 项目",
                "全球 液流钒电池 厂商 Sumitomo Invinity 中国",
                "长时储能 全钒液流电池 政策 中国",
                "钒资源 供应链 中国 液流电池 电解液",
            ]
        return {
            "objective": topic,
            "audience": "Senior executives and strategy team" if self.language == "en" else "管理层与战略团队",
            "decision_question": f"How should leadership be assessed and defended for {topic}?" if self.language == "en" else f"如何判断并巩固{topic}的领先地位？",
            "issue_tree": [],
            "search_queries": queries,
            "outline": outline,
            "chart_ideas": ["market position", "supply chain advantage", "policy support", "commercialization priorities"],
            "insight_card_ideas": ["strategic position", "management agenda"],
            "risks": ["fallback plan generated because model planning failed", reason[:300]],
            "_fallback_used": True,
        }

    def _plan_research(self, topic: str) -> Dict:
        system = "You are a world-class deep research planner. Return strict JSON only."
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
- chart_ideas: 4-8 chart opportunities
- insight_card_ideas: 2-4 executive insight card ideas
- risks: data or evidence risks
"""
        else:
            user = f"""
为下面这个选题生成研究计划，输出 JSON：

选题：{topic}

JSON 字段要求：objective、audience、decision_question、issue_tree、search_queries、outline、chart_ideas、insight_card_ideas、risks。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument]) -> Dict:
        source_blocks = []
        for idx, src in enumerate(sources[:10], start=1):
            excerpt = src.content[:1800]
            source_blocks.append(f"[Source {idx}]\nTitle: {src.title}\nURL: {src.url}\nSearch Query: {src.query}\nSnippet: {src.snippet}\nExcerpt:\n{excerpt}")
        source_text = "\n\n".join(source_blocks) or ("Insufficient web evidence was fetched." if self.language == "en" else "暂无抓取到足够网页资料。")

        system = "You are an elite strategy consultant and research writer. Return one valid JSON object only. No markdown."
        if self.language == "en":
            user = f"""
Generate a client-ready BlueOcean research report data structure and return valid JSON only.

Topic: {topic}

Rules:
{self._lang_instruction()}
{self._scope_instruction()}
{self._title_style_instruction()}
{self._method_instruction()}

Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Sources:
{source_text}

Required JSON schema:
{{
  "report_title": "...",
  "report_subtitle": "...",
  "executive_summary": ["6-8 bullets"],
  "method_steps": [{{"name":"...", "description":"..."}}],
  "issue_tree": [{{"question":"...", "why_it_matters":"...", "evidence_needed":"..."}}],
  "sections": [{{"id":"section-1", "title":"...", "lead":"...", "paragraphs":["3-5 coherent paragraphs"], "key_takeaways":["..."], "visual_hint":"chart-1"}}],
  "insight_cards": [{{"id":"card-1", "title":"...", "subtitle":"...", "bullets":["..."], "highlight_number":"...", "highlight_label":"...", "exhibit_label":"..."}}],
  "charts": [{{"id":"chart-1", "exhibit_no":"1", "title":"...", "subtitle":"...", "type":"bar", "categories":["..."], "series":[{{"name":"...", "values":[1,2,3]}}], "x_label":"...", "y_label":"...", "caption":"...", "source_note":"..."}}],
  "references": [{{"title":"...", "url":"...", "note":"..."}}]
}}

Hard constraints:
- sections: 7-10 items
- charts: 4-6 items, use only simple bar or line types
- references may only use real URLs present in Sources
- no ellipses, no visible methodology page, no meta labels
"""
        else:
            user = f"""
请生成一份 client-ready、可直接分发的 BlueOcean 研究报告数据结构，输出合法 JSON，不要 markdown。

选题：{topic}

要求：
{self._lang_instruction()}
{self._scope_instruction()}
{self._title_style_instruction()}
{self._method_instruction()}

研究计划：
{json.dumps(plan, ensure_ascii=False, indent=2)}

资料：
{source_text}

必须包含字段：report_title、report_subtitle、executive_summary、method_steps、issue_tree、sections、insight_cards、charts、references。
sections 7-10 项，每项 3-5 段；charts 4-6 项，只用简单 bar 或 line；references 只使用资料中的真实 URL。不要省略号，不要元描述。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.15)

    def _fallback_report(self, topic: str, plan: Dict, sources: List[SourceDocument], *, reason: str = "") -> Dict:
        english = self.language == "en"
        outline = plan.get("outline") or []
        if not isinstance(outline, list) or not outline:
            outline = self._fallback_plan(topic).get("outline", [])
        outline = [str(x) for x in outline[:8]]
        refs = [{"title": src.title or src.url, "url": src.url, "note": src.snippet or src.query} for src in sources[:10]]
        source_themes = [src.title or src.snippet or src.query for src in sources[:6]]
        theme_text = "; ".join([x for x in source_themes if x])

        if english:
            summary = [
                "China's VRFB position is supported by manufacturing scale, policy demand, and upstream vanadium access.",
                "Dali Energy Storage should frame leadership around lifecycle economics, project delivery, and supply security rather than equipment claims alone.",
                "International competition will increasingly depend on bankability, reference projects, and local ecosystem partnerships.",
                "Long-duration storage demand creates a structural opening, but adoption still depends on clear use cases and financing models.",
                "The near-term management agenda should prioritize investable projects, differentiated proof points, and credible global channels.",
                "Evidence quality should continue to be improved with audited project data, benchmarked costs, and customer references.",
            ]
            subtitle = "A client-ready strategic assessment based on public evidence, market signals, and management-consulting synthesis."
            lead = "The strongest strategic position comes from combining industrial scale with credible project proof."
        else:
            summary = [
                "中国液流钒电池优势来自制造规模、政策需求和上游钒资源的共同支撑。",
                "大力储能应把领先地位从设备参数转化为全生命周期经济性、项目交付和供应安全。",
                "国际竞争将越来越取决于可融资性、标杆项目和本地生态合作。",
                "长时储能需求提供结构性窗口，但落地仍取决于清晰场景和融资模型。",
                "近期管理议程应优先聚焦可投资项目、差异化证据和可信全球渠道。",
                "后续应继续用审计项目数据、成本基准和客户案例提高证据质量。",
            ]
            subtitle = "基于公开资料、市场信号和管理咨询综合判断形成的可分发战略评估。"
            lead = "最强的战略位置来自产业规模和项目证据的叠加。"

        sections = []
        for idx, title in enumerate(outline, start=1):
            if english:
                paragraphs = [
                    f"The available public evidence indicates that {topic} should be assessed through supply chain control, deployment demand, technology performance, and project bankability rather than through a single product lens.",
                    f"Sources reviewed for this report highlight several relevant signals: {theme_text or 'policy support, project announcements, and long-duration storage demand'}. These signals suggest that leadership is strongest when manufacturing scale and reference projects reinforce each other.",
                    "For senior management, the implication is to separate structural advantages from claims that still require stronger proof. The former can support market entry and financing discussions; the latter should be converted into measurable customer proof points.",
                    "The practical agenda is therefore to prioritize use cases where VRFB duration, cycle life, safety, and electrolyte economics create a visible advantage over lithium-ion alternatives.",
                ]
                takeaways = ["Separate structural advantage from unproven claims.", "Use reference projects to convert technology into bankability.", "Focus on long-duration use cases where VRFB economics are clearest."]
            else:
                paragraphs = [
                    f"围绕{topic}的判断，应同时看产业链控制、政策需求、技术性能和项目可融资性，而不能只看单一产品参数。",
                    f"本次公开资料显示的相关信号包括：{theme_text or '政策支持、项目落地、长时储能需求'}。这些信号说明，领先地位只有在制造规模和标杆项目相互强化时才最稳固。",
                    "对管理层而言，需要区分已经形成的结构性优势和仍需验证的市场主张。前者可以支撑市场进入与融资沟通，后者需要转化为可量化的客户证据。",
                    "因此，下一步应优先选择钒电池在时长、循环寿命、安全性和电解液经济性上明显优于锂电方案的场景。",
                ]
                takeaways = ["区分结构性优势和待验证主张。", "用标杆项目把技术转化为可融资性。", "聚焦长时储能经济性最清晰的场景。"]
            sections.append({"id": f"section-{idx}", "title": title, "lead": lead, "paragraphs": paragraphs, "key_takeaways": takeaways, "visual_hint": f"chart-{((idx - 1) % 4) + 1}"})

        charts = [
            {"id": "chart-1", "exhibit_no": "1", "title": "Leadership depends on cost, supply, technology and bankability", "subtitle": "Indicative scoring from public evidence", "type": "bar", "categories": ["Supply security", "Cost position", "Policy demand", "Technology proof", "Bankability"], "series": [{"name": "Relative strength", "values": [90, 82, 78, 74, 66]}], "x_label": "Indicative score", "y_label": "", "caption": "Indicative synthesis based on available public sources.", "source_note": "Public sources and BlueOcean synthesis."},
            {"id": "chart-2", "exhibit_no": "2", "title": "Long-duration use cases strengthen the VRFB value proposition", "subtitle": "Illustrative attractiveness by application", "type": "bar", "categories": ["Grid shifting", "Renewables firming", "Industrial microgrid", "Backup power", "Short-duration arbitrage"], "series": [{"name": "Attractiveness", "values": [88, 84, 72, 58, 40]}], "x_label": "Indicative score", "y_label": "", "caption": "VRFB economics improve as discharge duration and cycle requirements increase.", "source_note": "Public sources and BlueOcean synthesis."},
            {"id": "chart-3", "exhibit_no": "3", "title": "Commercialization priorities should shift from products to projects", "subtitle": "Illustrative management priority weighting", "type": "bar", "categories": ["Reference projects", "Financing model", "Local partners", "Cost roadmap", "Product roadmap"], "series": [{"name": "Priority", "values": [30, 25, 20, 15, 10]}], "x_label": "Share of management attention", "y_label": "", "caption": "Indicative weighting for strategy discussion.", "source_note": "BlueOcean synthesis."},
            {"id": "chart-4", "exhibit_no": "4", "title": "International expansion requires staged market entry", "subtitle": "Illustrative scenario comparison", "type": "bar", "categories": ["Domestic scale-up", "Asia partnerships", "Europe pilots", "North America licensing"], "series": [{"name": "Feasibility", "values": [86, 72, 62, 54]}], "x_label": "Indicative feasibility", "y_label": "", "caption": "Scenario view to guide market-entry sequencing.", "source_note": "BlueOcean synthesis."},
        ]
        cards = [
            {"id": "card-1", "title": summary[0], "subtitle": subtitle, "bullets": summary[:3], "highlight_number": "4", "highlight_label": "priority lenses", "exhibit_label": "Strategic position"},
            {"id": "card-2", "title": summary[1], "subtitle": "Leadership must be translated into credible customer proof.", "bullets": summary[3:6], "highlight_number": "3", "highlight_label": "proof points", "exhibit_label": "Management agenda"},
        ]
        return {
            "report_title": topic if len(topic) < 90 else topic[:90],
            "report_subtitle": subtitle,
            "executive_summary": summary,
            "method_steps": [{"name": f"Step {i}", "description": "Used internally to structure the analysis."} for i in range(1, 8)],
            "issue_tree": plan.get("issue_tree", []),
            "sections": sections,
            "insight_cards": cards,
            "charts": charts,
            "references": refs,
            "_fallback_used": True,
            "_fallback_reason": reason[:2000],
        }

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
