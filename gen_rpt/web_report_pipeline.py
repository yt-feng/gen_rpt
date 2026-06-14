from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .brand_assets import copy_or_generate_brand_assets, write_reference_backup
from .deepseek_client import DeepSeekClient
from .graphics import ensure_dir
from .image_generator import generate_ai_image_assets
from .research_quality import ResearchFactPack, build_research_fact_pack
from .web_fetch import SourceDocument, collect_sources
from .web_report_renderer import render_web_report_html, render_web_report_markdown


class WebReportPipeline:
    """HTML-first research report pipeline.

    The legacy ResearchPipeline treats HTML as an intermediate artifact for PDF.
    This pipeline treats the browser article as the primary product and keeps
    PDF/PPT concerns out of the content schema.
    """

    def __init__(self, client: DeepSeekClient, language: str = "en") -> None:
        self.client = client
        self.language = "zh" if str(language or "").lower().startswith("zh") else "en"

    def build_report(self, topic: str, output_dir: Path) -> Dict[str, Any]:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)
        display_topic = str(topic or "").strip()

        try:
            plan = self._plan_research(display_topic)
        except Exception as exc:
            (output_dir / "web_plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(display_topic, str(exc))

        per_query = int(os.getenv("GEN_RPT_PER_QUERY", "5"))
        max_sources = int(os.getenv("GEN_RPT_MAX_SOURCES", "20"))
        sources = collect_sources(plan.get("search_queries", [])[:8], per_query=per_query, max_sources=max_sources)
        source_dicts = [source.__dict__ for source in sources]
        fact_pack = build_research_fact_pack(display_topic, plan, sources)

        try:
            report = self._synthesize_web_report(display_topic, plan, sources, fact_pack)
        except Exception as exc:
            (output_dir / "web_synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            report = self._fallback_report(display_topic, plan, sources, fact_pack, str(exc))

        self._post_process(report, display_topic, sources, fact_pack)

        assets = copy_or_generate_brand_assets(assets_dir)
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)
        assets.update(
            generate_ai_image_assets(
                self.client,
                display_topic,
                report,
                assets_dir,
                Path(backup_dir),
                language=self.language,
            )
        )

        html_path = render_web_report_html(report, assets, output_dir / "index.html", display_topic, self.language)
        markdown_path = render_web_report_markdown(report, output_dir / "report.md", display_topic, self.language)

        (output_dir / "web_report_payload.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "plan": plan,
            "fact_pack": fact_pack.to_dict(),
            "sources": source_dicts,
            "report": report,
            "assets": assets,
            "output_dir": str(output_dir),
            "html_path": str(html_path),
            "markdown_path": str(markdown_path),
            "backup_dir": str(backup_dir),
        }

    def _plan_research(self, topic: str) -> Dict[str, Any]:
        system = "You are a senior research planner for a BCG-style digital publication. Return strict JSON only."
        if self.language == "zh":
            user = f"""
为一个 HTML-first 深度分析网页生成研究计划，输出 JSON。

主题：{topic}

必须包含字段：objective、audience、decision_question、issue_tree、search_queries、source_strategy、outline、exhibit_ideas、risks。
要求：
- search_queries 8-10 条，优先能找到政府、监管、公司公告、年报、行业协会、国际组织、权威媒体、学术或咨询机构资料。
- outline 4-6 个章节，标题必须是结论先行。
- exhibit_ideas 3-5 个，不要装饰图；每个图都要回答一个管理层问题。
- 明确哪些信息需要数字、案例、时间线或反例来验证。
"""
        else:
            user = f"""
Create a research plan for an HTML-first deep analysis article and return JSON only.

Topic: {topic}

Required fields: objective, audience, decision_question, issue_tree, search_queries, source_strategy, outline, exhibit_ideas, risks.
Requirements:
- 8-10 public-web search queries, prioritizing government, regulators, company filings, annual reports, industry associations, international organizations, authoritative media, academic sources and consulting research.
- 4-6 conclusion-first outline headings.
- 3-5 exhibit ideas. No decorative visuals; every exhibit must answer an executive question.
- State what needs numbers, cases, timeline evidence or counter-evidence.
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)

    def _synthesize_web_report(
        self,
        topic: str,
        plan: Dict[str, Any],
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
    ) -> Dict[str, Any]:
        source_blocks = []
        for idx, source in enumerate(sources[:14], start=1):
            source_blocks.append(
                f"[Source {idx}]\n"
                f"Title: {source.title}\n"
                f"URL: {source.url}\n"
                f"Domain: {source.domain}\n"
                f"Type: {source.source_type}\n"
                f"Snippet: {source.snippet}\n"
                f"Excerpt:\n{source.content[:2200]}"
            )
        source_text = "\n\n".join(source_blocks) or ("No reliable source text was fetched." if self.language == "en" else "未抓取到可靠资料正文。")
        system = "You are an elite strategy research author. Return one valid JSON object only. No markdown."
        if self.language == "zh":
            user = f"""
生成一份 HTML-first、类似 BCG publication 的深度分析网页报告数据结构，输出 JSON。

主题：{topic}
研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}
事实包：{fact_pack.digest()}
资料摘录：
{source_text}

必须包含字段：
title、dek、category、authors、intro、key_takeaways、sections、exhibits、action_steps、methodology、evidence_quality、references、disclaimer。

写作要求：
- 全程中文，面向 CEO/董事会/战略团队。
- 内容质量优先于长度；目标是 4-6 个扎实章节，而不是很多浅章节。
- title 和章节标题必须结论先行；不要用“概览、背景、趋势、分析、结论”这类标签标题。
- key_takeaways 3 条，每条必须有明确判断和管理含义。
- sections 4-6 个；每个包含 title、lead、paragraphs、evidence、so_what。每章 5-7 段，必须包含数字、日期、案例、机制或反例中的至少两类。
- exhibits 3-5 个；使用 metric_row、bar、line、matrix、process、bubble 中的类型。只有资料支持时才给真实数字；如果是方向性评分，必须在 caption/source_note 明确写“方向性评分/需复核”，不能伪装成事实。
- action_steps 3-5 个，每个包含 horizon、action、success_metric。
- references 只能使用上方 Sources 中真实 URL。
- 不要暴露内部提示、不要说“本章节认为/本报告认为”，直接写判断。
- 缺失的市场规模、份额、ROI、成本等不要编造，写成证据缺口和核验任务。
"""
        else:
            user = f"""
Generate an HTML-first, BCG-publication-like deep analysis report data structure and return JSON.

Topic: {topic}
Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}
Fact pack:
{fact_pack.digest()}
Source excerpts:
{source_text}

Required fields:
title, dek, category, authors, intro, key_takeaways, sections, exhibits, action_steps, methodology, evidence_quality, references, disclaimer.

Writing rules:
- English only. Write for a CEO, board and strategy team audience.
- Depth matters more than page count. Aim for 4-6 substantial sections rather than many shallow chapters.
- The title and every section title must be conclusion-first. Avoid label headings such as Overview, Background, Trends, Analysis or Conclusion.
- key_takeaways: exactly 3, each with a clear claim and management implication.
- sections: 4-6 items. Each has title, lead, paragraphs, evidence, so_what. Each section needs 5-7 paragraphs and must include at least two of: numbers, dates, cases, causal mechanism, counter-evidence.
- exhibits: 3-5 items using metric_row, bar, line, matrix, process or bubble. Use real numbers only when supported by sources. If using directional scores, label them as directional in caption/source_note; do not disguise them as facts.
- action_steps: 3-5 items, each with horizon, action, success_metric.
- references may only use real URLs present in Sources.
- Do not expose internal prompt language. Do not write "this section argues" or "this report finds"; state the insight directly.
- Do not fabricate market size, share, ROI or cost data. If missing, keep it as an evidence gap and validation task.
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.12)

    def _post_process(
        self,
        report: Dict[str, Any],
        topic: str,
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
    ) -> None:
        report.setdefault("title", topic)
        report.setdefault("category", "Deep research" if self.language == "en" else "深度研究")
        report["source_count"] = fact_pack.source_count
        if not report.get("evidence_quality"):
            if fact_pack.validation_issues:
                report["evidence_quality"] = " ".join(fact_pack.validation_issues[:3])
            else:
                report["evidence_quality"] = (
                    f"Evidence base includes {fact_pack.source_count} public sources and {fact_pack.authoritative_source_count} authority-weighted sources."
                    if self.language == "en"
                    else f"资料底座包含 {fact_pack.source_count} 个公开来源，其中 {fact_pack.authoritative_source_count} 个具有权威来源特征。"
                )
        real_urls = {source.url for source in sources if source.url}
        refs = []
        for item in report.get("references", []) or []:
            if isinstance(item, dict):
                url = str(item.get("url") or "")
                if url in real_urls:
                    refs.append(item)
        if not refs:
            refs = [{"title": source.title or source.domain or source.url, "url": source.url, "note": source.snippet} for source in sources[:14]]
        report["references"] = refs
        report.setdefault(
            "disclaimer",
            "Prepared for strategy discussion; validate source data before investment, transaction or operating decisions."
            if self.language == "en"
            else "本报告用于战略讨论；用于投资、交易或运营决策前需独立核验来源数据。",
        )

    def _fallback_plan(self, topic: str, reason: str) -> Dict[str, Any]:
        if self.language == "zh":
            queries = [f"{topic} 市场 数据", f"{topic} 政策", f"{topic} 公司 年报", f"{topic} 行业协会", f"{topic} 研究报告"]
            outline = ["该议题首先需要回答管理层决策问题", "证据质量决定可以投入多少资源", "价值创造路径需要拆成可验证假设", "下一步行动应围绕证据缺口推进"]
        else:
            queries = [f"{topic} market data", f"{topic} policy", f"{topic} company annual report", f"{topic} industry association", f"{topic} research report"]
            outline = ["The topic needs to be framed as an executive decision", "Evidence quality determines how much commitment is justified", "The value path should be split into testable assumptions", "Next moves should close the evidence gaps first"]
        return {
            "objective": topic,
            "audience": "CEO, board and strategy team" if self.language == "en" else "CEO、董事会和战略团队",
            "decision_question": topic,
            "issue_tree": [],
            "search_queries": queries,
            "source_strategy": "fallback",
            "outline": outline,
            "exhibit_ideas": [],
            "risks": [reason[:300]],
            "_fallback_used": True,
        }

    def _fallback_report(
        self,
        topic: str,
        plan: Dict[str, Any],
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
        reason: str,
    ) -> Dict[str, Any]:
        if self.language == "zh":
            takeaways = [
                f"{topic} 需要先被定义为管理层决策问题，而不是资料摘要。",
                "公开证据不足时，最重要的不是写得更满，而是保留证据边界和核验任务。",
                "下一步应围绕客户价值、成本、竞争和执行能力关闭关键证据缺口。",
            ]
            sections = [
                {
                    "title": "证据边界决定管理层可以多快行动",
                    "lead": "如果公开资料无法支持市场规模、份额、成本或 ROI，报告必须把这些内容保留为待核验假设。",
                    "paragraphs": [
                        "当前兜底版本优先保护事实边界。它不会把模型推断改写成确定事实，也不会用泛化图表填充页面。",
                        "管理层真正需要的是知道哪些判断已经有来源支持，哪些仍只是方向性假设。",
                        "后续应优先补充政府、监管、年报、公告、行业协会和客户案例资料。",
                        "网页化输出的优势是可以保留更清楚的来源区、行动模块和证据边界，而不受 PDF 页高限制。",
                        "这条路径更适合做持续迭代的深度报告。每次新增资料后，可以更新事实包、图表和行动建议。",
                    ],
                    "evidence": fact_pack.validation_issues[:4],
                    "so_what": "先关闭证据缺口，再扩大叙事和设计投入。",
                }
            ]
            methodology = f"兜底报告生成原因：{reason[:240]}。"
        else:
            takeaways = [
                f"{topic} should first be framed as an executive decision, not a source summary.",
                "When public evidence is thin, the right move is to preserve the source boundary and validation tasks.",
                "Next work should close evidence gaps around customer value, cost, competition and execution capacity.",
            ]
            sections = [
                {
                    "title": "Evidence boundaries determine how fast leadership can move",
                    "lead": "If public sources do not support market size, share, cost or ROI, the report should keep those points as validation tasks.",
                    "paragraphs": [
                        "This fallback draft protects the factual boundary first. It does not convert model inference into sourced fact or use generic exhibits to fill space.",
                        "The management need is to know which claims are source-backed and which remain directional assumptions.",
                        "Follow-up research should prioritize government, regulator, filing, annual-report, industry-association and customer-case evidence.",
                        "The HTML-first path can expose source boundaries, action modules and evidence gaps more clearly than a fixed-height PDF page.",
                        "That makes it better suited to an iterative deep-research workflow in which every new source can improve the fact pack, exhibits and management agenda.",
                    ],
                    "evidence": fact_pack.validation_issues[:4],
                    "so_what": "Close the evidence gaps before spending more effort on narrative polish or design.",
                }
            ]
            methodology = f"Fallback report generated because synthesis failed: {reason[:240]}."
        return {
            "title": topic,
            "dek": takeaways[0],
            "category": "Deep research" if self.language == "en" else "深度研究",
            "authors": ["BlueOcean Research"],
            "intro": [takeaways[0]],
            "key_takeaways": takeaways,
            "sections": sections,
            "exhibits": [],
            "action_steps": [],
            "methodology": methodology,
            "evidence_quality": " ".join(fact_pack.validation_issues[:3]),
            "references": [{"title": source.title or source.url, "url": source.url, "note": source.snippet} for source in sources[:10]],
            "_fallback_used": True,
        }
