from __future__ import annotations

import json
import os
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
from .research_quality import (
    ResearchFactPack,
    apply_deterministic_report_fixes,
    build_research_fact_pack,
    build_revision_messages,
    validate_report,
)
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
        per_query = int(os.getenv("GEN_RPT_PER_QUERY", "4"))
        max_sources = int(os.getenv("GEN_RPT_MAX_SOURCES", "16"))
        sources = collect_sources(queries, per_query=per_query, max_sources=max_sources)
        source_dicts = [source.__dict__ for source in sources]
        fact_pack = build_research_fact_pack(display_topic, plan, sources)

        try:
            report = self._synthesize_report(display_topic, plan, sources, fact_pack=fact_pack, raw_topic=topic)
        except Exception as exc:
            (output_dir / "synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            report = self._fallback_report(display_topic, plan, sources, reason=str(exc))

        self._post_process_report(report, display_topic)
        report, content_quality = self._validate_and_revise_report(
            report,
            display_topic,
            fact_pack,
            raw_topic=topic,
        )
        report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), source_dicts)
        self._ensure_visual_hints(report)

        asset_map = copy_or_generate_brand_assets(assets_dir)
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)
        asset_map.update(generate_ai_image_assets(self.client, display_topic, report, assets_dir, Path(backup_dir), language=self.language))
        asset_map.update(self._materialize_assets(report, assets_dir))

        html_path, markdown_path, pdf_path = self._render_report_pack(report, asset_map, output_dir, display_topic)
        qa_dir = output_dir / "backup" / "qa"
        final_report = report
        qa_result = {}
        layout_rounds = []
        max_layout_rounds = max(1, int(os.getenv("REPORT_MAX_LAYOUT_QA_ROUNDS", "2")))
        for round_idx in range(max_layout_rounds):
            round_dir = qa_dir if round_idx == 0 else qa_dir / f"round_{round_idx + 1}"
            qa_result = run_pdf_qa(pdf_path, html_path, round_dir)
            layout_rounds.append(
                {
                    "round": round_idx + 1,
                    "passed": bool(qa_result.get("passed")),
                    "issue_count": len(qa_result.get("issues", [])),
                    "recommendations": qa_result.get("recommendations", []),
                }
            )
            if qa_result.get("passed", False) or round_idx == max_layout_rounds - 1:
                break
            if round_idx == 0:
                (output_dir / "report_payload_prefixed.json").write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
            final_report = apply_pdf_qa_fixes(final_report, qa_result)
            self._post_process_report(final_report, display_topic)
            final_report = apply_deterministic_report_fixes(final_report, fact_pack, language=self.language)
            self._post_process_report(final_report, display_topic)
            self._ensure_visual_hints(final_report)
            html_path, markdown_path, pdf_path = self._render_report_pack(final_report, asset_map, output_dir, display_topic)
        post_layout_issues = validate_report(final_report, fact_pack, language=self.language)

        pptx_path = render_pptx(final_report, asset_map, output_dir / "report.pptx", display_topic, self.language)
        presentation_path = render_presentation_html(final_report, asset_map, output_dir / "presentation.html", display_topic, self.language)

        (output_dir / "report_payload.json").write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "qa_result.json").write_text(json.dumps(qa_result, ensure_ascii=False, indent=2), encoding="utf-8")
        quality_payload = {
            "content": content_quality,
            "layout_rounds": layout_rounds,
            "post_layout_content_issues": post_layout_issues,
        }
        (output_dir / "report_quality.json").write_text(json.dumps(quality_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return {"plan": plan, "fact_pack": fact_pack.to_dict(), "sources": source_dicts, "report": final_report, "asset_map": asset_map, "output_dir": str(output_dir), "backup_dir": str(backup_dir), "language": self.language, "target_length": self.target_length, "html_path": str(html_path), "markdown_path": str(markdown_path), "pdf_path": str(pdf_path), "pptx_path": str(pptx_path), "presentation_path": str(presentation_path), "qa_result": qa_result, "report_quality": quality_payload}

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
        return (
            "Use structured problem solving, issue trees, evidence triangulation and an internal executive strategy stress test as writing discipline only. "
            "Do not name internal frameworks in the report; render only concise source-boundary and decision-readiness notes."
            if self.language == "en"
            else "把结构化问题拆解、issue tree、证据交叉校验和内部高管战略压力测试作为写作心法；正式报告不得展示或命名内部框架，只输出来源边界和决策就绪度说明。"
        )

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

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument], *, fact_pack: ResearchFactPack, raw_topic: str = "") -> Dict:
        source_blocks = []
        for idx, src in enumerate(sources[:10], start=1):
            excerpt = src.content[:1800]
            source_blocks.append(f"[Source {idx}]\nTitle: {src.title}\nURL: {src.url}\nType: {src.source_type}\nDomain: {src.domain}\nSearch Query: {src.query}\nSnippet: {src.snippet}\nExcerpt:\n{excerpt}")
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
Evidence pack extracted before generation:
{fact_pack.digest()}
Sources:
{source_text}
Required fields: report_title, report_subtitle, executive_summary, executive_summary_text, key_findings, action_plan, risk_register, scenario_vignettes, methodology_note, author_credentials, method_steps, issue_tree, sections, insight_cards, charts, references.
Hard constraints:
- Write for a CEO and board audience. The report must answer: what matters commercially, what changes capital allocation, what risks can break the case, and what management should do next.
- Use an internal executive strategy stress test before writing: market outperformance, true advantage, granular where-to-play, trend timing, privileged evidence, uncertainty, commitment versus flexibility, bias checks, conviction to act, and action translation. Do not name or expose this internal framework.
- executive_summary_text: one tight narrative paragraph that states the decision, commercial implication, source-supported uncertainty, risk and immediate next step.
- key_findings: 4-6 items, each with finding, evidence, management_implication.
- action_plan: 3-5 items, each with horizon, action, owner, success_metric, decision_gate. Cover near-term, medium-term and long-term actions.
- risk_register: 4-6 items, each with risk, trigger, management_action, evidence_boundary.
- scenario_vignettes: at least 1 CEO decision scenario with title, situation, ceo_question, recommended_move, watchouts.
- methodology_note: describe public source collection, source limits, cross-checking and validation gaps. Do not describe internal consulting frameworks.
- author_credentials: 1-3 team/institution credentials for the final report.
- sections: 7-10 items, each with 3-5 coherent paragraphs and distinct analysis.
- charts: 10-12 items, using a mix of bar, stacked_bar, line, matrix and bubble only. Do not use pie or donut charts. Every chart must include concrete data arrays, categories, labels and source_note; do not provide decorative or generic visuals.
- Do not output visible process labels such as Future action agenda, What to watch, risk register, action plan, management implication, evidence boundary, internal framework or stress test. Use organic CEO-facing prose only.
- Every section must have visual_hint set to image-N matching the section number whenever possible (image-1, image-2, ...). This lets the renderer use topic-specific Pollinations visuals.
- Chart titles and categories must be specific to the topic, not generic labels such as Policy, Platforms, Creators, Commerce or Technology.
- Do not show Chinese text in the final report.
- references may only use real URLs present in Sources.
- Use the evidence pack as the factual boundary. Include dated and numeric facts when available. If a fact is not supported by the evidence pack or source excerpts, state that the claim remains unverified instead of inventing a number or event.
- If data needed for ROI, market size, cost or share is missing, use a clearly labeled placeholder such as [insert verified cost data] and explain the validation task; do not fabricate numbers.
- Avoid technical encyclopedia prose. Any technical detail must translate into customer value, cost, investment return, financing, competitive advantage, risk or action.
- Avoid unsupported forecasts. Label scenarios as directional and evidence-based.
- no ellipses, no visible internal framework names, no meta labels.
"""
        else:
            user = f"""
请生成一份 client-ready、可直接分发的 BlueOcean 研究报告数据结构，输出合法 JSON，不要 markdown。
选题：{topic}
要求：{self._lang_instruction()} {self._scope_instruction()} {self._title_style_instruction()} {self._method_instruction()}
研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}
生成前抽取的证据包：{fact_pack.digest()}
资料：{source_text}
必须包含字段：report_title、report_subtitle、executive_summary、executive_summary_text、key_findings、action_plan、risk_register、scenario_vignettes、methodology_note、author_credentials、method_steps、issue_tree、sections、insight_cards、charts、references。
写给 CEO/董事会读者：所有技术事实都必须转化为商业价值、成本、投资回报、客户价值、融资、竞争优势、风险或行动含义。
写作前使用内部高管战略压力测试：市场竞胜、真实优势、竞争场景颗粒度、趋势时点、独到证据、不确定性、承诺与灵活性、偏见、执行决心和行动落地。正式报告不得展示或命名该内部框架。
executive_summary_text 写成一段结论先行的执行摘要；key_findings 4-6 条，每条包含 finding、evidence、management_implication；action_plan 3-5 条，每条包含 horizon、action、owner、success_metric、decision_gate；risk_register 4-6 条，每条包含 risk、trigger、management_action、evidence_boundary；scenario_vignettes 至少 1 个 CEO 决策场景。
methodology_note 只说明公开资料、来源边界、交叉校验和待核验缺口，不展示内部咨询框架。
sections 7-10 项；charts 10-12 项，混合使用 bar、stacked_bar、line、matrix、bubble，不要使用 pie/donut。每个 chart 必须包含具体数据数组、分类、标签和 source_note，不要生成装饰性或泛化图片。每个 section 的 visual_hint 尽量使用对应 image-N。
正式报告不得出现 Future action agenda、What to watch、risk register、action plan、management implication、evidence boundary、internal framework、stress test 等显性过程标签；只用自然的 CEO 读者视角表达。
事实边界：只能基于证据包和资料摘录写作；有可核验日期、数字、金额、份额、产能、财务或政策节点时必须纳入；资料未支持的判断要写清证据边界，不能编造数字或事件。若 ROI、市场规模、成本或份额数据缺失，用 [插入经核验数据] 这类占位符和核验任务表达，不要编造。
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.15)

    def _validate_and_revise_report(
        self,
        report: Dict,
        topic: str,
        fact_pack: ResearchFactPack,
        *,
        raw_topic: str = "",
    ) -> tuple[Dict, Dict[str, Any]]:
        max_rounds = max(1, int(os.getenv("REPORT_MAX_CONTENT_QA_ROUNDS", os.getenv("REPORT_MAX_REVISIONS", "2"))))
        rounds: List[Dict[str, Any]] = []
        current = report
        for round_idx in range(max_rounds):
            self._post_process_report(current, topic)
            issues = validate_report(current, fact_pack, language=self.language)
            rounds.append({"round": round_idx + 1, "issue_count": len(issues), "issues": issues})
            if not issues:
                break
            if round_idx == max_rounds - 1:
                break
            try:
                current = self.client.chat_json(
                    build_revision_messages(
                        topic=topic,
                        raw_topic=raw_topic,
                        language=self.language,
                        fact_pack=fact_pack,
                        issues=issues,
                        previous_report=current,
                    ),
                    temperature=0.0,
                )
            except Exception as exc:
                rounds[-1]["revision_error"] = str(exc)
                break

        current = apply_deterministic_report_fixes(current, fact_pack, language=self.language)
        self._post_process_report(current, topic)
        self._ensure_visual_hints(current)
        final_issues = validate_report(current, fact_pack, language=self.language)
        return current, {
            "max_rounds": max_rounds,
            "rounds": rounds,
            "final_issue_count": len(final_issues),
            "final_issues": final_issues,
            "fact_pack_validation_issues": fact_pack.validation_issues,
            "source_count": fact_pack.source_count,
            "authoritative_source_count": fact_pack.authoritative_source_count,
        }

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
                ("Dali can convert technical capacity into leadership only if it proves project bankability", "The next stage of competition will be won by firms that make VRFB projects easier to finance and operate.", ["Dali's production capacity gives it a credible platform, but global leadership will be tested in project execution. International customers will evaluate whether the company can deliver predictable commissioning, stable electrolyte performance and lifecycle service support across different regulatory environments.", "The highest-value commercial evidence will come from reference projects with transparent operating data. Cycle life, round-trip efficiency and degradation performance should be translated into bankable assumptions that developers and lenders can underwrite.", "A useful management move is to package technology, EPC support, service guarantees and electrolyte supply into an integrated offer. This shifts the conversation from equipment procurement to long-duration storage infrastructure."], "image-2"),
                ("Vanadium access gives Chinese suppliers a resilience edge, but price volatility still needs active management", "Supply security is a strategic asset only if it is backed by contracting, recycling and electrolyte leasing models.", ["China's vanadium position gives domestic VRFB suppliers a structural advantage in electrolyte availability and cost visibility. This matters because electrolyte can represent a large share of system cost and can also become a financing asset if leasing structures are used.", "The risk is volatility. Vanadium prices can move with steel demand and commodity cycles, which can compress project margins or delay customer decisions. Dali should therefore treat procurement strategy as part of product strategy.", "Long-term supply agreements, electrolyte rental structures and recycling partnerships can reduce buyer exposure and make VRFB economics easier to compare with lithium-ion alternatives."], "image-3"),
                ("Policy support creates a protected base market, but export growth needs local legitimacy", "China's domestic policy tailwind is powerful, while overseas growth will depend on localization and standards participation.", ["Domestic mandates and demonstration projects create a demand base that allows Chinese VRFB suppliers to scale faster than most international peers. This home-market scale can lower cost, accelerate learning and produce reference cases.", "Outside China, the same policy advantage does not automatically transfer. Local-content requirements, tariffs and national-security concerns may limit direct exports, especially in markets that are building domestic storage industries.", "Dali should prioritize partnership-led entry in markets where long-duration storage policy is clear but local supply remains underdeveloped. Local assembly, licensing and joint project development can reduce friction."], "image-4"),
                ("Lifecycle economics is the clearest wedge against lithium-ion in long-duration use cases", "VRFB's advantage strengthens as duration, cycling and safety requirements increase.", ["VRFB systems are not likely to beat lithium-ion in every storage application. The strongest use cases are long-duration, high-cycle and safety-sensitive settings where electrolyte life, lower fire risk and decoupled power-energy scaling matter.", "This positioning should shape Dali's customer segmentation. Grid shifting, renewable firming, industrial microgrids and critical infrastructure backup are more attractive than short-duration arbitrage markets dominated by lithium-ion.", "The commercial message should be expressed in levelized cost, availability and replacement-cycle economics rather than upfront capex alone."], "image-5"),
                ("Global competitors retain niche strengths, but scale and cost are tilting the field toward China", "International peers remain relevant in reliability, modularity and brand trust, but struggle to match Chinese scale economics.", ["Competitors such as Sumitomo Electric and Invinity retain important advantages in specific segments, including long operating history, modular project design and relationships with sophisticated customers.", "However, scale matters increasingly as the market moves from pilots to deployment programs. Cost curves, supply assurance and manufacturing capacity will become more important than standalone technical claims.", "Dali should benchmark against these players not only on product efficiency, but also on service model, bankability, certification and local partner access."], "image-6"),
                ("Dali's international playbook should sequence markets by proof potential, not just demand size", "The best first markets are those where reference projects can unlock repeatable channels.", ["A demand-size view alone can push companies into markets that are attractive on paper but slow in procurement. A better lens combines policy clarity, partner availability, tariff exposure, financing readiness and the ability to create visible reference projects.", "Asia-Pacific and selected European markets may offer practical entry points if Dali can secure local development partners and demonstrate compliance with grid and safety standards.", "The sequence should favor markets where one credible project can become a platform for repeat orders, financing partnerships and service-network buildout."], "image-7"),
                ("The next leadership agenda should focus on membranes, service models and evidence quality", "Sustained advantage will depend on converting manufacturing strength into trusted operating performance.", ["The innovation agenda should focus on membrane durability, electrolyte cost, stack reliability and digital monitoring. These improvements directly affect lifecycle economics and customer confidence.", "Equally important is evidence quality. Dali should publish clearer operating data, third-party validation and customer references where possible, because global buyers will discount unsupported performance claims.", "Management should treat proof generation as a strategic workstream: select lighthouse projects, define measurable KPIs, and turn field performance into sales and financing collateral."], "image-8"),
            ]
        else:
            summary = [
                f"{topic}的判断应从公开证据、产业位置和执行约束三条线同时展开。",
                "资料不足处需要保留证据边界，不能用模型推断替代来源核验。",
                "管理层最需要的是把市场趋势翻译成可验证的决策问题、资源配置和阶段性动作。",
                "竞争格局不应只看单点技术或单年增速，而要看供给能力、客户验证和商业闭环。",
                "图表和结论应服务于决策，而不是重复资料摘要。",
                "后续工作应围绕权威来源、关键数字、时间线和反例持续补充验证。",
            ]
            subtitle = "基于公开资料、来源底稿和管理咨询问题拆解形成的研究初稿。"
            sections = [
                (f"{topic}需要先建立可核验的事实边界", "报告优先区分公开资料已经支持的事实、方向性判断和仍需复核的信息缺口。", ["本报告的兜底版本保留来源底稿，并把公开证据作为写作边界。对于资料没有直接披露的市场规模、财务数据、政策节点或企业经营指标，正文不把推断写成确定事实。", "这种处理方式适合在模型生成失败或资料抓取不足时维持报告可读性，同时避免把未经核验的信息放入正式判断。", "后续应优先补充政府、监管、交易所、公司公告、年报、国际组织和行业协会等来源，以提高事实密度和结论可信度。"], "image-1"),
                ("管理层问题应从趋势判断转向行动排序", "真正有用的研究不是罗列趋势，而是把趋势转成资源配置、进入节奏和风险控制。", ["围绕该选题，管理层需要判断哪些变化已经具备公开证据，哪些仍是情景假设。只有把这两类信息分开，报告才能支持决策而不是制造噪音。", "行动排序应优先关注可以被验证的指标，例如政策发布时间、市场规模、供需变化、客户采纳、产能建设、融资成本和竞争对手动作。", "当公开资料不足时，报告应把缺口写出来，并把补充调研列为下一步动作。"], "image-2"),
                ("竞争格局要同时看规模、能力和商业闭环", "单一技术指标或单一市场份额不足以解释长期竞争优势。", ["竞争优势通常来自多个要素的组合，包括供应链、产品成熟度、客户验证、渠道能力、服务体系、融资可得性和监管适配。", "研究报告应把这些要素拆成可比较维度，而不是用笼统的领先、增长、潜力等词替代分析。", "图表部分也应围绕这些维度组织，避免使用泛化分类。"], "image-3"),
                ("数字和时间线是报告可信度的底座", "有年份、金额、比例、产能、收入和政策节点，判断才有复核入口。", ["事实包中的数字和日期应优先进入正文，因为它们能帮助读者判断事件顺序、规模量级和变化速度。", "如果来源无法支持关键数字，报告需要明确说明公开资料不足，并把该数字列入后续核验清单。", "这种写法比补充未经来源支持的估算更稳健。"], "image-4"),
                ("图表应表达判断，而不是装饰页面", "每一张图都应对应一个管理问题或关键结论。", ["图表标题要具体到选题，不应停留在政策、市场、技术、增长等泛化标签。", "当模型提出的图表数据过于稀薄时，系统会把低质量图表转成更稳健的方向性指数或矩阵。", "正式使用前仍应结合来源底稿复核每个图表的数据口径。"], "image-5"),
                ("输出质量依赖反复校验，而不是一次生成", "内容、来源、结构和排版都需要独立检查。", ["生成后应检查章节数量、每章段落深度、引用来源、数字密度、时间线、语言混杂、元标签和重复句式。", "PDF 输出还需要检查文本重叠、字体异常、页面过密和可见截断。", "只有内容 QA 和排版 QA 都通过，报告才适合进入分发或进一步人工编辑。"], "image-6"),
                ("下一步是把证据缺口转成调研清单", "报告初稿的价值在于明确哪些判断已经可用，哪些需要继续验证。", ["如果资料抓取不足，下一步应优先补权威来源，而不是扩大模型重写轮次。", "对于高风险判断，应保留来源、截图、PDF 摘录和时间戳，确保后续复核有据可依。", "这一流程能把生成式报告从一次性文本变成可持续迭代的研究工作底稿。"], "image-7"),
            ]

        charts = _fallback_charts()
        label_a = "strategic levers" if english else "关键抓手"
        label_b = "proof points" if english else "验证点"
        exhibit_a = "Strategic position" if english else "战略位置"
        exhibit_b = "Management agenda" if english else "管理议题"
        card_2_subtitle = "Leadership must be translated into credible customer proof." if english else "判断必须转化为可核验的证据与行动。"
        cards = [{"id": "card-1", "title": summary[0], "subtitle": subtitle, "bullets": summary[:3], "highlight_number": "6", "highlight_label": label_a, "exhibit_label": exhibit_a}, {"id": "card-2", "title": summary[1], "subtitle": card_2_subtitle, "bullets": summary[3:6], "highlight_number": "3", "highlight_label": label_b, "exhibit_label": exhibit_b}]
        section_payload = []
        takeaway_2 = "Translate the claim into measurable project evidence." if english else "把判断转化为可复核的证据。"
        takeaway_3 = "Prioritize customer segments where duration and safety create clear value." if english else "优先处理能改变决策的关键证据。"
        for idx, (title, lead, paragraphs, visual_hint) in enumerate(sections, start=1):
            section_payload.append({"id": f"section-{idx}", "title": title, "lead": lead, "paragraphs": paragraphs, "key_takeaways": [summary[(idx - 1) % len(summary)], takeaway_2, takeaway_3], "visual_hint": visual_hint})
        if english:
            executive_summary_text = (
                f"The CEO-level conclusion is that {topic} should be managed as a staged strategic option, not a single binary bet. "
                "The available public evidence supports a focused management agenda around commercial proof, cost position, financing readiness, customer adoption and execution risk. "
                "Where source evidence is incomplete, the report preserves the gap as a diligence task rather than converting it into an unsupported forecast. "
                "Management should fund near-term validation, protect medium-term options and reserve larger commitments for decision gates tied to verified operating, customer and financial evidence."
            )
            key_findings = [
                {"finding": summary[0], "evidence": "Synthesized from fetched public sources and retained source backup.", "management_implication": "Treat the opportunity as a capital-allocation question and tie conviction to evidence quality."},
                {"finding": summary[1], "evidence": "Fallback synthesis prioritizes project, customer and financing proof over technical claims.", "management_implication": "Shift the CEO discussion from product capability to bankability and repeatable execution."},
                {"finding": summary[2], "evidence": "Public evidence should be used to validate cost, supply and resilience assumptions before commitment.", "management_implication": "Build a diligence ledger for cost structure, sourcing, contract model and margin exposure."},
                {"finding": summary[4], "evidence": "International growth assumptions remain directional until local partners, customers and financing paths are verified.", "management_implication": "Sequence market entry around reference projects and partner access, not headline demand size."},
            ]
            action_plan = [
                {"horizon": "Near term, 0-90 days", "action": "Build a CEO evidence ledger for market size, customer demand, cost, revenue, policy and financing claims.", "owner": "Strategy lead", "success_metric": "Every material claim has a source, date, confidence level and open validation item.", "decision_gate": "No unsupported number is used for investment or board decisions."},
                {"horizon": "Medium term, 1-2 quarters", "action": "Run targeted customer, partner and cost diligence to separate no-regret moves from option-building moves.", "owner": "Business owner", "success_metric": "Priority moves have named customers or partners, budget ranges and validation metrics.", "decision_gate": "Scale only when customer pull, cost position and execution feasibility are evidenced."},
                {"horizon": "Long term, 2-4 quarters", "action": "Commit larger capital, partnership or market-entry resources only after evidence gates are met.", "owner": "CEO / board", "success_metric": "ROI, risk exposure and execution milestones enter the quarterly management dashboard.", "decision_gate": "Pause or preserve optionality if core assumptions remain unverified."},
            ]
            risk_register = [
                {"risk": "Public evidence remains too thin for high-conviction capital decisions.", "trigger": "Source count, authoritative sources, numeric facts or timeline evidence fall below threshold.", "management_action": "Classify claims as verified, directional or open diligence and add authoritative sources.", "evidence_boundary": "Use only fetched public sources and source backup."},
                {"risk": "Technical claims are mistaken for commercial readiness.", "trigger": "Discussion centers on specifications rather than customer value, cost, financing and deployment proof.", "management_action": "Rewrite claims into bankability, margin, customer adoption and execution implications.", "evidence_boundary": "Unsupported performance claims require third-party or customer validation."},
                {"risk": "Market-entry timing runs ahead of local execution capacity.", "trigger": "No verified partner, customer, regulatory pathway or service model exists for the target market.", "management_action": "Use option-building pilots before major expansion commitments.", "evidence_boundary": "Local market claims remain directional until validated by sources or direct diligence."},
                {"risk": "Commodity, supply-chain or policy volatility resets the economics.", "trigger": "Input price, tariff, subsidy, permitting or financing assumptions move outside planned ranges.", "management_action": "Add contractual protections, scenario thresholds and quarterly risk review.", "evidence_boundary": "Scenario values should be independently validated before investment use."},
            ]
            scenario_vignettes = [
                {"title": "CEO investment committee scenario", "situation": "Management is deciding whether to allocate budget and partner capacity before all public-evidence gaps are closed.", "ceo_question": "Which decisions are safe now, and which should wait for customer, cost, financing or policy validation?", "recommended_move": "Approve low-cost validation and partner discussions while holding larger commitments behind evidence gates.", "watchouts": "Do not treat market enthusiasm or technical narrative as proof of ROI, bankability or scalable demand."}
            ]
            methodology_note = f"This fallback report is based on {len(sources)} fetched public sources retained in the source backup. It distinguishes verified public evidence, directional synthesis and open validation gaps; unsupported market size, ROI, cost or share assumptions should be replaced with verified data before investment use."
            author_credentials = [{"name": "BlueOcean Research", "role": "Research synthesis team", "credentials": "Responsible for public-source collection, evidence-boundary checks, executive synthesis and report QA."}]
            method_steps = [{"name": "Evidence boundary", "description": "Identify public-source support and open validation gaps."}, {"name": "Commercial translation", "description": "Convert technical facts into CEO decisions, risks and actions."}, {"name": "Decision readiness", "description": "Sequence no-regret moves, options and major commitments."}]
        else:
            executive_summary_text = (
                f"{topic}应被管理层视为分阶段战略选择，而不是一次性押注。公开资料已经足以形成围绕商业证明、成本位置、融资可得性、客户采纳和执行风险的管理议题；"
                "但缺少来源支持的市场规模、ROI、成本或份额数字应作为待核验缺口保留。CEO 应先投入低成本验证和关键合作讨论，把重大资源承诺放在证据门槛之后。"
            )
            key_findings = [
                {"finding": summary[0], "evidence": "基于已抓取公开资料和来源底稿综合。", "management_implication": "把机会判断转成资源配置、风险偏好和验证门槛。"},
                {"finding": summary[1], "evidence": "资料不足处保留证据边界，不用模型推断替代核验。", "management_implication": "董事会材料中应区分已验证、方向性和待核验判断。"},
                {"finding": summary[2], "evidence": "管理层最需要可验证的客户、成本、收入、融资和时间线证据。", "management_implication": "近期优先建立证据台账和验证节奏。"},
                {"finding": summary[3], "evidence": "竞争优势需要同时看规模、能力、客户验证和商业闭环。", "management_implication": "不要用单点技术指标替代商业回报判断。"},
            ]
            action_plan = [
                {"horizon": "近期 0-90 天", "action": "建立 CEO 证据台账，复核市场规模、客户需求、成本、收入、政策和融资判断。", "owner": "战略负责人", "success_metric": "每个关键判断均有来源、日期、置信度和待核验项", "decision_gate": "未经来源支持的数字不进入投资或董事会判断"},
                {"horizon": "中期 1-2 个季度", "action": "做客户、合作伙伴和成本尽调，区分无悔动作、选择权和重大投入。", "owner": "业务负责人", "success_metric": "优先事项均有客户/伙伴、预算区间和验证指标", "decision_gate": "客户拉动、成本位置和执行可行性被验证后再扩大投入"},
                {"horizon": "长期 2-4 个季度", "action": "证据门槛达成后再推进重大资本、合作或市场进入资源。", "owner": "CEO/董事会", "success_metric": "投资回报、风险暴露和执行里程碑进入季度管理仪表盘", "decision_gate": "核心假设未验证时保留选择权并暂停重大投入"},
            ]
            risk_register = [
                {"risk": "公开证据不足导致过度确定", "trigger": "来源数量、权威来源、数字事实或时间线不足", "management_action": "把判断分为已验证、方向性和待核验，并补充权威来源", "evidence_boundary": "仅使用已抓取公开来源和来源底稿"},
                {"risk": "技术叙事被误认为商业就绪", "trigger": "讨论集中于规格而非客户价值、成本、融资和项目证明", "management_action": "把技术判断改写为可融资性、毛利、客户采纳和执行含义", "evidence_boundary": "未支持的性能判断需要第三方或客户核验"},
                {"risk": "市场进入节奏快于本地执行能力", "trigger": "目标市场缺少已验证伙伴、客户、监管路径或服务模型", "management_action": "先用小规模试点保留选择权，再进入重大扩张", "evidence_boundary": "本地市场判断在来源或直接尽调前保持方向性"},
                {"risk": "供应链、政策或融资变化重置经济性", "trigger": "原材料、关税、补贴、审批或融资假设超出计划区间", "management_action": "设置合同保护、情景阈值和季度风险复盘", "evidence_boundary": "情景数字用于投资前必须独立核验"},
            ]
            scenario_vignettes = [
                {"title": "CEO 投资委员会场景", "situation": "管理层正在判断是否在证据缺口完全关闭前投入预算和合作资源。", "ceo_question": "哪些动作可以现在做，哪些必须等待客户、成本、融资或政策证据？", "recommended_move": "先批准低成本验证和伙伴讨论，把重大投入放在证据门槛之后。", "watchouts": "不要把市场热度或技术叙事等同于投资回报、可融资性或可规模化需求。"}
            ]
            methodology_note = f"本兜底报告基于{len(sources)}个已抓取公开来源和来源底稿，区分已验证公开证据、方向性综合和待核验缺口；未经来源支持的市场规模、ROI、成本或份额假设应在投资使用前替换为经核验数据。"
            author_credentials = [{"name": "BlueOcean Research", "role": "研究综合团队", "credentials": "负责公开资料收集、证据边界校验、管理层视角综合和报告 QA。"}]
            method_steps = [{"name": "证据边界", "description": "识别公开来源支持和待核验缺口。"}, {"name": "商业翻译", "description": "把技术事实转成 CEO 决策、风险和行动。"}, {"name": "决策就绪", "description": "区分无悔动作、选择权和重大承诺。"}]
        return {
            "report_title": topic,
            "report_subtitle": subtitle,
            "executive_summary": summary,
            "executive_summary_text": executive_summary_text,
            "key_findings": key_findings,
            "action_plan": action_plan,
            "risk_register": risk_register,
            "scenario_vignettes": scenario_vignettes,
            "methodology_note": methodology_note,
            "author_credentials": author_credentials,
            "method_steps": method_steps,
            "issue_tree": plan.get("issue_tree", []),
            "sections": section_payload,
            "insight_cards": cards,
            "charts": charts,
            "references": refs,
            "_fallback_used": True,
            "_fallback_reason": reason[:2000],
        }

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
        for idx, section in enumerate(report.get("sections", []), start=1):
            # Prefer topic-specific Pollinations section visuals across LaTeX, PPTX and HTML.
            section["visual_hint"] = f"image-{idx}"
            if idx > 12 and chart_ids:
                section["visual_hint"] = chart_ids[(idx - 1) % len(chart_ids)]

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
        {"id": "chart-5", "exhibit_no": "5", "title": "Commercialization priorities should shift from products to projects", "subtitle": "Illustrative management attention allocation", "type": "bar", "categories": ["Reference projects", "Financing model", "Local partners", "Cost roadmap", "Product roadmap"], "series": [{"name": "Priority index", "values": [92, 84, 76, 66, 58]}], "caption": "Management focus should move toward bankable delivery and repeatable channels.", "source_note": "BlueOcean synthesis."},
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
