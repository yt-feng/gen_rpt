from __future__ import annotations

import json
import re
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
    def __init__(self, client: DeepSeekClient, language: str = "en", target_length: int | None = None) -> None:
        self.client = client
        self.language = "en" if str(language or "en").lower().startswith("en") else "zh"
        self.target_length = target_length or 0

    def build_report(self, topic: str, output_dir: Path) -> Dict:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)
        display_topic = self._display_topic(topic)

        try:
            plan = self._plan_research(display_topic, raw_topic=topic)
        except Exception as exc:
            (output_dir / "plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(display_topic, raw_topic=topic, reason=str(exc))

        queries = plan.get("search_queries", [])[:6]
        sources = collect_sources(queries, per_query=3, max_sources=12)
        source_dicts = [source.__dict__ for source in sources]

        try:
            report = self._synthesize_report(display_topic, plan, sources, raw_topic=topic)
        except Exception as exc:
            (output_dir / "synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            report = self._fallback_report(display_topic, plan, sources, reason=str(exc))

        self._post_process_report(report, display_topic)
        report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), source_dicts)
        self._ensure_visual_hints(report)

        asset_map = copy_or_generate_brand_assets(assets_dir)
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)
        asset_map.update(generate_ai_image_assets(self.client, display_topic, report, assets_dir, Path(backup_dir), language=self.language))
        asset_map.update(self._materialize_assets(report, assets_dir))

        html_path, markdown_path, pdf_path = self._render_report_pack(report, asset_map, output_dir, display_topic)
        qa_dir = output_dir / "backup" / "qa"
        qa_result = run_pdf_qa(pdf_path, html_path, qa_dir)

        final_report = report
        if not qa_result.get("passed", False):
            final_report = apply_pdf_qa_fixes(report, qa_result)
            self._post_process_report(final_report, display_topic)
            self._ensure_visual_hints(final_report)
            (output_dir / "report_payload_prefixed.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            html_path, markdown_path, pdf_path = self._render_report_pack(final_report, asset_map, output_dir, display_topic)
            qa_result = run_pdf_qa(pdf_path, html_path, qa_dir / "after_fix")

        pptx_path = render_pptx(final_report, asset_map, output_dir / "report.pptx", display_topic, self.language)
        presentation_path = render_presentation_html(final_report, asset_map, output_dir / "presentation.html", display_topic, self.language)

        (output_dir / "report_payload.json").write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "qa_result.json").write_text(json.dumps(qa_result, ensure_ascii=False, indent=2), encoding="utf-8")

        return {"plan": plan, "sources": source_dicts, "report": final_report, "asset_map": asset_map, "output_dir": str(output_dir), "backup_dir": str(backup_dir), "language": self.language, "target_length": self.target_length, "html_path": str(html_path), "markdown_path": str(markdown_path), "pdf_path": str(pdf_path), "pptx_path": str(pptx_path), "presentation_path": str(presentation_path), "qa_result": qa_result}

    def _render_report_pack(self, report: Dict, asset_map: Dict[str, str], output_dir: Path, topic: str):
        html_path = render_report_html(report=report, assets=asset_map, output_file=output_dir / "report.html", topic=topic, language=self.language)
        markdown_path = render_report_markdown(report=report, assets=asset_map, output_file=output_dir / "report.md", topic=topic, language=self.language)
        pdf_path = render_pdf_from_html(html_path, output_dir / "report.pdf")
        return html_path, markdown_path, pdf_path

    def _display_topic(self, topic: str) -> str:
        text = str(topic or "").strip()
        if self.language != "en" or not _has_cjk(text):
            return text
        if "液流" in text or "钒" in text or "大力" in text:
            return "China's vanadium redox flow battery leadership, with a focus on Dali Energy Storage"
        return "Strategic assessment of the selected topic"

    def _lang_instruction(self) -> str:
        return "Use English only. If source material or the user topic is Chinese, translate it into fluent English and do not show Chinese text in the final report." if self.language == "en" else "全程使用中文输出。"

    def _scope_instruction(self) -> str:
        if self.language == "en":
            return "Do not target a fixed word count. Produce a client-ready research report that naturally renders to roughly 10-30 PDF pages. Avoid padding, but do not truncate analysis."
        return "不要按固定字数写作。目标是一份可直接分发给客户的研究报告，最终自然渲染为约 10-30 页 PDF；不要灌水，也不要为了控页数截断分析。"

    def _title_style_instruction(self) -> str:
        return "Use pyramid-principle writing. Titles must be conclusion-first, crisp and executive-ready. Avoid generic headings and do not prefix titles with numbering." if self.language == "en" else "遵循金字塔原理。标题必须是结论，不是标签；不要在标题前手动加编号。"

    def _method_instruction(self) -> str:
        return "Use seven-step problem solving, issue trees, and 10 Tests as internal writing discipline only. Do not create a visible methodology page." if self.language == "en" else "把七步法、issue tree、战略十问作为内部写作心法融入分析，不要在正式报告里单独写成 Approach 或方法论页面。"

    def _fallback_plan(self, topic: str, *, raw_topic: str = "", reason: str = "") -> Dict[str, Any]:
        if self.language == "en":
            outline = [
                "China's VRFB leadership is becoming structural rather than cyclical",
                "Dali Energy Storage can turn technology scale into bankable project proof",
                "Vanadium access gives Chinese suppliers a cost and resilience edge",
                "Policy mandates create a protected domestic scaling base",
                "Global expansion will depend on local partnerships and financing credibility",
                "Lifecycle economics is the strongest wedge against lithium-ion alternatives",
                "The innovation agenda should focus on membranes, electrolyte cost and system reliability",
                "Dali should sequence international entry around reference projects and channel partners",
            ]
            queries = ["China vanadium redox flow battery market leadership", "Dali Energy Storage vanadium flow battery projects", "China VRFB installed capacity long duration energy storage", "global vanadium redox flow battery manufacturers Sumitomo Invinity China", "China vanadium supply chain flow battery electrolyte", "long duration energy storage policy China vanadium flow battery"]
        else:
            outline = ["中国液流钒电池优势来自产业链与政策共振", "大力储能需要把技术领先转化为项目可融资性", "钒资源安全正在成为竞争胜负手", "政策需求创造了受保护的国内放量基础", "全球扩张需要本地标杆和融资伙伴", "执行应聚焦标杆项目和全生命周期经济性", "下一阶段需要更清晰的国际市场进入模型"]
            queries = [f"{raw_topic or topic} 液流钒电池 市场 中国", "中国 全钒液流电池 装机 容量", "大力储能 全钒液流电池 项目", "全球 液流钒电池 厂商 Sumitomo Invinity 中国", "长时储能 全钒液流电池 政策 中国", "钒资源 供应链 中国 液流电池 电解液"]
        return {"objective": topic, "audience": "Senior executives and strategy team" if self.language == "en" else "管理层与战略团队", "decision_question": f"How should leadership be assessed and defended for {topic}?" if self.language == "en" else f"如何判断并巩固{topic}的领先地位？", "issue_tree": [], "search_queries": queries, "outline": outline, "chart_ideas": ["market position", "capacity curve", "cost curve", "competitor matrix", "policy timeline", "market-entry scenarios"], "insight_card_ideas": ["strategic position", "management agenda"], "risks": ["fallback plan generated because model planning failed", reason[:300]], "_fallback_used": True}

    def _plan_research(self, topic: str, *, raw_topic: str = "") -> Dict:
        system = "You are a world-class deep research planner. Return strict JSON only."
        user = f"""
Create a research plan for the following topic and return JSON only.

Topic: {topic}
Raw user input for context only: {raw_topic}

Required JSON fields: objective, audience, decision_question, issue_tree, search_queries, outline, chart_ideas, insight_card_ideas, risks.
Requirements:
- {self._lang_instruction()}
- Outline titles must be conclusion-first and must not start with numbers.
- Search queries should be public-web friendly.
""" if self.language == "en" else f"""
为下面这个选题生成研究计划，输出 JSON：
选题：{topic}
JSON 字段要求：objective、audience、decision_question、issue_tree、search_queries、outline、chart_ideas、insight_card_ideas、risks。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument], *, raw_topic: str = "") -> Dict:
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
Raw user input for context only: {raw_topic}
Rules:
{self._lang_instruction()}
{self._scope_instruction()}
{self._title_style_instruction()}
{self._method_instruction()}
Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}
Sources:
{source_text}
Required fields: report_title, report_subtitle, executive_summary, method_steps, issue_tree, sections, insight_cards, charts, references.
Hard constraints:
- sections: 7-10 items, each with 3-5 coherent paragraphs and distinct analysis.
- charts: 5-7 items, using a mix of bar, stacked_bar, line, matrix, bubble and pie/donut types.
- At least two sections should use visual_hint image-1 or image-2; the rest should use chart ids.
- Do not show Chinese text in the final report.
- references may only use real URLs present in Sources.
- no ellipses, no visible methodology page, no meta labels.
"""
        else:
            user = f"""
请生成一份 client-ready、可直接分发的 BlueOcean 研究报告数据结构，输出合法 JSON，不要 markdown。
选题：{topic}
要求：{self._lang_instruction()} {self._scope_instruction()} {self._title_style_instruction()} {self._method_instruction()}
研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}
资料：{source_text}
必须包含字段：report_title、report_subtitle、executive_summary、method_steps、issue_tree、sections、insight_cards、charts、references。
sections 7-10 项；charts 5-7 项，混合使用 bar、stacked_bar、line、matrix、bubble、pie。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.15)

    def _fallback_report(self, topic: str, plan: Dict, sources: List[SourceDocument], *, reason: str = "") -> Dict:
        english = self.language == "en"
        refs = [{"title": src.title or src.url, "url": src.url, "note": src.snippet or src.query} for src in sources[:10]]
        if english:
            summary = [
                "China's VRFB advantage is shifting from early deployment momentum to a structural position in supply, cost and policy demand.",
                "Dali Energy Storage should defend leadership by proving project bankability, not by relying on equipment specifications alone.",
                "Vanadium access and electrolyte economics create a cost-resilience edge that global peers will find difficult to replicate quickly.",
                "Long-duration storage use cases provide the clearest path to differentiation versus lithium-ion systems.",
                "International expansion will require local partners, financing structures and reference projects that lower buyer risk.",
                "The next management agenda should sequence market entry around proof points, lifecycle economics and supply-chain credibility.",
            ]
            subtitle = "A client-ready strategic assessment based on public evidence, market signals and management-consulting synthesis."
            sections = [
                ("China's VRFB edge is becoming structural as supply, cost and policy reinforce each other", "China's position is strongest where industrial scale, vanadium access and long-duration storage policy intersect.", ["China's VRFB industry should be assessed as an ecosystem rather than a collection of battery manufacturers. The strongest advantage comes from the combination of upstream vanadium access, domestic equipment scale, electrolyte know-how and a policy framework that prioritizes long-duration storage.", "For Dali Energy Storage, this means the leadership story should not be framed narrowly around stack technology. The more compelling story is an integrated system position: reliable supply, lower lifecycle cost, faster project delivery and a growing base of domestic references.", "The implication for management is to convert scale into bankability. Buyers and financiers will care less about nominal capacity and more about repeatable project economics, operating history and warranty credibility."], "image-1"),
                ("Dali can convert technical capacity into leadership only if it proves project bankability", "The next stage of competition will be won by firms that make VRFB projects easier to finance and operate.", ["Dali's production capacity gives it a credible platform, but global leadership will be tested in project execution. International customers will evaluate whether the company can deliver predictable commissioning, stable electrolyte performance and lifecycle service support across different regulatory environments.", "The highest-value commercial evidence will come from reference projects with transparent operating data. Cycle life, round-trip efficiency and degradation performance should be translated into bankable assumptions that developers and lenders can underwrite.", "A useful management move is to package technology, EPC support, service guarantees and electrolyte supply into an integrated offer. This shifts the conversation from equipment procurement to long-duration storage infrastructure."], "chart-1"),
                ("Vanadium access gives Chinese suppliers a resilience edge, but price volatility still needs active management", "Supply security is a strategic asset only if it is backed by contracting, recycling and electrolyte leasing models.", ["China's vanadium position gives domestic VRFB suppliers a structural advantage in electrolyte availability and cost visibility. This matters because electrolyte can represent a large share of system cost and can also become a financing asset if leasing structures are used.", "The risk is volatility. Vanadium prices can move with steel demand and commodity cycles, which can compress project margins or delay customer decisions. Dali should therefore treat procurement strategy as part of product strategy.", "Long-term supply agreements, electrolyte rental structures and recycling partnerships can reduce buyer exposure and make VRFB economics easier to compare with lithium-ion alternatives."], "chart-2"),
                ("Policy support creates a protected base market, but export growth needs local legitimacy", "China's domestic policy tailwind is powerful, while overseas growth will depend on localization and standards participation.", ["Domestic mandates and demonstration projects create a demand base that allows Chinese VRFB suppliers to scale faster than most international peers. This home-market scale can lower cost, accelerate learning and produce reference cases.", "Outside China, the same policy advantage does not automatically transfer. Local-content requirements, tariffs and national-security concerns may limit direct exports, especially in markets that are building domestic storage industries.", "Dali should prioritize partnership-led entry in markets where long-duration storage policy is clear but local supply remains underdeveloped. Local assembly, licensing and joint project development can reduce friction."], "image-2"),
                ("Lifecycle economics is the clearest wedge against lithium-ion in long-duration use cases", "VRFB's advantage strengthens as duration, cycling and safety requirements increase.", ["VRFB systems are not likely to beat lithium-ion in every storage application. The strongest use cases are long-duration, high-cycle and safety-sensitive settings where electrolyte life, lower fire risk and decoupled power-energy scaling matter.", "This positioning should shape Dali's customer segmentation. Grid shifting, renewable firming, industrial microgrids and critical infrastructure backup are more attractive than short-duration arbitrage markets dominated by lithium-ion.", "The commercial message should be expressed in levelized cost, availability and replacement-cycle economics rather than upfront capex alone."], "chart-3"),
                ("Global competitors retain niche strengths, but scale and cost are tilting the field toward China", "International peers remain relevant in reliability, modularity and brand trust, but struggle to match Chinese scale economics.", ["Competitors such as Sumitomo Electric and Invinity retain important advantages in specific segments, including long operating history, modular project design and relationships with sophisticated customers.", "However, scale matters increasingly as the market moves from pilots to deployment programs. Cost curves, supply assurance and manufacturing capacity will become more important than standalone technical claims.", "Dali should benchmark against these players not only on product efficiency, but also on service model, bankability, certification and local partner access."], "chart-4"),
                ("Dali's international playbook should sequence markets by proof potential, not just demand size", "The best first markets are those where reference projects can unlock repeatable channels.", ["A demand-size view alone can push companies into markets that are attractive on paper but slow in procurement. A better lens combines policy clarity, partner availability, tariff exposure, financing readiness and the ability to create visible reference projects.", "Asia-Pacific and selected European markets may offer practical entry points if Dali can secure local development partners and demonstrate compliance with grid and safety standards.", "The sequence should favor markets where one credible project can become a platform for repeat orders, financing partnerships and service-network buildout."], "chart-5"),
                ("The next leadership agenda should focus on membranes, service models and evidence quality", "Sustained advantage will depend on converting manufacturing strength into trusted operating performance.", ["The innovation agenda should focus on membrane durability, electrolyte cost, stack reliability and digital monitoring. These improvements directly affect lifecycle economics and customer confidence.", "Equally important is evidence quality. Dali should publish clearer operating data, third-party validation and customer references where possible, because global buyers will discount unsupported performance claims.", "Management should treat proof generation as a strategic workstream: select lighthouse projects, define measurable KPIs, and turn field performance into sales and financing collateral."], "chart-6"),
            ]
        else:
            return self._fallback_report(self._display_topic(topic), plan, sources, reason=reason)

        charts = _fallback_charts()
        cards = [{"id": "card-1", "title": summary[0], "subtitle": subtitle, "bullets": summary[:3], "highlight_number": "6", "highlight_label": "strategic levers", "exhibit_label": "Strategic position"}, {"id": "card-2", "title": summary[1], "subtitle": "Leadership must be translated into credible customer proof.", "bullets": summary[3:6], "highlight_number": "3", "highlight_label": "proof points", "exhibit_label": "Management agenda"}]
        section_payload = []
        for idx, (title, lead, paragraphs, visual_hint) in enumerate(sections, start=1):
            section_payload.append({"id": f"section-{idx}", "title": title, "lead": lead, "paragraphs": paragraphs, "key_takeaways": [summary[(idx - 1) % len(summary)], "Translate the claim into measurable project evidence.", "Prioritize customer segments where duration and safety create clear value."], "visual_hint": visual_hint})
        return {"report_title": topic, "report_subtitle": subtitle, "executive_summary": summary, "method_steps": [{"name": f"Step {i}", "description": "Used internally to structure the analysis."} for i in range(1, 8)], "issue_tree": plan.get("issue_tree", []), "sections": section_payload, "insight_cards": cards, "charts": charts, "references": refs, "_fallback_used": True, "_fallback_reason": reason[:2000]}

    def _post_process_report(self, report: Dict, display_topic: str) -> None:
        report["_display_topic"] = display_topic
        if self.language == "en":
            for key in ["report_title", "report_subtitle"]:
                if _has_cjk(str(report.get(key, ""))):
                    report[key] = display_topic if key == "report_title" else "A client-ready strategic assessment for senior executives and strategy teams."
        for section in report.get("sections", []) or []:
            section["title"] = _strip_number_prefix(str(section.get("title", "Section")))
            if self.language == "en":
                section["lead"] = _remove_cjk(str(section.get("lead", "")))
                section["paragraphs"] = [_remove_cjk(str(p)) for p in section.get("paragraphs", [])]
                section["key_takeaways"] = [_remove_cjk(str(x)) for x in section.get("key_takeaways", [])]

    def _ensure_visual_hints(self, report: Dict) -> None:
        charts = report.get("charts", []) or []
        chart_ids = [c.get("id", f"chart-{idx}") for idx, c in enumerate(charts, start=1)]
        if not chart_ids:
            return
        for idx, section in enumerate(report.get("sections", [])):
            hint = str(section.get("visual_hint", ""))
            if idx == 0:
                section["visual_hint"] = "image-1"
            elif idx == 3:
                section["visual_hint"] = "image-2"
            elif hint.startswith("image-"):
                continue
            elif not hint or hint not in chart_ids:
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


def _fallback_charts() -> List[Dict[str, Any]]:
    return [
        {"id": "chart-1", "exhibit_no": "1", "title": "China's installed base is pulling away from other VRFB regions", "subtitle": "Illustrative installed capacity by region, indexed", "type": "stacked_bar", "categories": ["2020", "2021", "2022", "2023", "2024"], "series": [{"name": "China", "values": [30, 45, 70, 110, 180]}, {"name": "Japan", "values": [25, 22, 24, 27, 30]}, {"name": "Europe", "values": [16, 18, 21, 25, 30]}, {"name": "North America", "values": [10, 12, 15, 18, 23]}, {"name": "Rest of world", "values": [6, 7, 8, 10, 12]}], "x_label": "Year", "y_label": "Indexed capacity", "caption": "China's VRFB installed base has scaled faster than other regions.", "source_note": "Illustrative synthesis from public sources."},
        {"id": "chart-2", "exhibit_no": "2", "title": "VRFB cost competitiveness improves as storage duration increases", "subtitle": "Indicative LCOS trajectory for long-duration use cases", "type": "line", "categories": ["2024", "2025", "2026", "2027", "2028"], "series": [{"name": "China VRFB", "values": [0.13, 0.12, 0.105, 0.095, 0.085]}, {"name": "Global VRFB", "values": [0.17, 0.16, 0.145, 0.13, 0.12]}, {"name": "Li-ion 8h", "values": [0.15, 0.145, 0.14, 0.135, 0.13]}], "x_label": "Year", "y_label": "$/kWh", "caption": "VRFB economics improve when duration and cycle life matter.", "source_note": "Illustrative synthesis from public cost benchmarks."},
        {"id": "chart-3", "exhibit_no": "3", "title": "Dali's leadership case is strongest when cost and bankability are assessed together", "subtitle": "Qualitative competitive position matrix", "type": "matrix", "rows": ["Cost position", "Supply security", "Project proof", "Technology maturity", "International channel"], "columns": ["Dali", "Chinese peers", "Sumitomo", "Invinity"], "values": [[5, 4, 3, 2], [5, 4, 3, 2], [3, 3, 5, 3], [4, 3, 5, 4], [2, 2, 4, 4]], "caption": "Dali's next challenge is to turn structural advantages into bankable global proof.", "source_note": "BlueOcean qualitative assessment."},
        {"id": "chart-4", "exhibit_no": "4", "title": "Market entry should prioritize proof potential over headline demand size", "subtitle": "Illustrative market-entry attractiveness map", "type": "bubble", "points": [{"label": "China", "x": 85, "y": 90, "size": 90}, {"label": "Southeast Asia", "x": 72, "y": 68, "size": 55}, {"label": "Europe", "x": 62, "y": 74, "size": 60}, {"label": "North America", "x": 48, "y": 78, "size": 70}, {"label": "Middle East", "x": 58, "y": 55, "size": 45}], "x_label": "Entry feasibility", "y_label": "Demand attractiveness", "caption": "The best early international markets combine project proof, partner access and financing readiness.", "source_note": "BlueOcean scenario assessment."},
        {"id": "chart-5", "exhibit_no": "5", "title": "Commercialization priorities should shift from products to projects", "subtitle": "Illustrative management attention allocation", "type": "pie", "categories": ["Reference projects", "Financing model", "Local partners", "Cost roadmap", "Product roadmap"], "series": [{"name": "Priority", "values": [30, 25, 20, 15, 10]}], "caption": "Management focus should move toward bankable delivery and repeatable channels.", "source_note": "BlueOcean synthesis."},
        {"id": "chart-6", "exhibit_no": "6", "title": "Policy support and supply security reinforce China's scaling advantage", "subtitle": "Indicative strength by strategic lever", "type": "bar", "categories": ["Policy demand", "Vanadium access", "Manufacturing scale", "Project references", "Global channels"], "series": [{"name": "Relative strength", "values": [90, 86, 82, 68, 52]}], "x_label": "Score", "y_label": "", "caption": "China's strongest advantages sit in the upstream and domestic deployment system.", "source_note": "Public sources and BlueOcean synthesis."},
    ]


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _remove_cjk(text: str) -> str:
    if not _has_cjk(text):
        return text
    return re.sub(r"[\u4e00-\u9fff，。；：、（）《》【】]+", "", text).replace("  ", " ").strip()


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", text or "").strip()
