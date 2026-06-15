from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .brand_assets import copy_or_generate_brand_assets, write_reference_backup
from .deepseek_client import DeepSeekClient
from .graphics import ensure_dir
from .image_generator import generate_ai_image_assets
from .research_quality import ResearchFactPack, build_research_fact_pack
from .web_evidence import build_evidence_exhibits, build_evidence_ledger, build_storyline_plan, merge_evidence_exhibits
from .web_fetch import SourceDocument, collect_sources
from .web_report_renderer import normalize_web_report, render_web_report_html, render_web_report_markdown


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
        run_start = time.monotonic()
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)
        display_topic = str(topic or "").strip()
        self._log(f"START web report pipeline | topic={display_topic!r} | output_dir={output_dir}")
        self._log("ETA planning=15-90s, chart_data_needs=10-60s, source_collection=45-240s, evidence=5-15s, synthesis=60-180s, visuals=60-360s")

        phase_start = time.monotonic()
        self._log("PHASE planning started | expected 15-90s")
        try:
            plan = self._plan_research(display_topic)
        except Exception as exc:
            (output_dir / "web_plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(display_topic, str(exc))
            self._log(f"PHASE planning fallback used | reason={str(exc)[:240]!r}")
        self._log(
            "PHASE planning completed "
            f"| elapsed={self._elapsed(phase_start)} | queries={len(plan.get('search_queries', []) or [])} "
            f"| outline={len(plan.get('outline', []) or [])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE chart_data_needs started | expected 10-60s")
        try:
            chart_data_needs = self._plan_chart_data_needs(display_topic, plan)
        except Exception as exc:
            (output_dir / "chart_data_needs_error.txt").write_text(str(exc), encoding="utf-8")
            chart_data_needs = self._fallback_chart_data_needs(display_topic, plan, str(exc))
            self._log(f"PHASE chart_data_needs fallback used | reason={str(exc)[:240]!r}")
        if not chart_data_needs:
            chart_data_needs = self._fallback_chart_data_needs(display_topic, plan, "model returned no chart data needs")
            self._log("PHASE chart_data_needs fallback used | reason='model returned no chart data needs'")
        chart_queries = self._chart_need_queries(chart_data_needs)
        self._log(
            "PHASE chart_data_needs completed "
            f"| elapsed={self._elapsed(phase_start)} | needs={len(chart_data_needs)} | chart_queries={len(chart_queries)}"
        )

        per_query = int(os.getenv("GEN_RPT_PER_QUERY", "5"))
        max_sources = int(os.getenv("GEN_RPT_MAX_SOURCES", "20"))
        max_queries = int(os.getenv("GEN_RPT_MAX_QUERIES", "12"))
        search_queries = self._expanded_search_queries(plan, chart_data_needs)[:max_queries]
        phase_start = time.monotonic()
        self._log(
            "PHASE source_collection started "
            f"| expected 45-240s | queries={len(search_queries)} | per_query={per_query} | max_sources={max_sources}"
        )
        if search_queries:
            self._log("PHASE source_collection query_plan | " + " || ".join(query[:120] for query in search_queries[:10]))
        sources = collect_sources(search_queries, per_query=per_query, max_sources=max_sources)
        source_dicts = [source.__dict__ for source in sources]
        domains = sorted({source.domain for source in sources if source.domain})
        self._log(
            "PHASE source_collection completed "
            f"| elapsed={self._elapsed(phase_start)} | sources={len(sources)} | domains={', '.join(domains[:8]) or 'none'}"
        )

        phase_start = time.monotonic()
        self._log("PHASE fact_pack started | expected <10s")
        fact_pack = build_research_fact_pack(display_topic, plan, sources)
        self._log(
            "PHASE fact_pack completed "
            f"| elapsed={self._elapsed(phase_start)} | source_count={fact_pack.source_count} "
            f"| authoritative={fact_pack.authoritative_source_count}"
        )

        phase_start = time.monotonic()
        self._log("PHASE evidence_ledger_and_storyline started | expected 5-15s")
        try:
            evidence_ledger = build_evidence_ledger(display_topic, sources, fact_pack)
        except Exception as exc:
            (output_dir / "web_evidence_error.txt").write_text(str(exc), encoding="utf-8")
            evidence_ledger = []
            self._log(f"PHASE evidence_ledger fallback used | reason={str(exc)[:240]!r}")
        storyline_plan = build_storyline_plan(display_topic, plan, fact_pack, evidence_ledger, language=self.language)
        family_counts: Dict[str, int] = {}
        for item in evidence_ledger:
            family = str(item.get("metric_family") or "other")
            family_counts[family] = family_counts.get(family, 0) + 1
        family_summary = ", ".join(f"{key}:{value}" for key, value in sorted(family_counts.items(), key=lambda x: (-x[1], x[0]))[:6])
        self._log(
            "PHASE evidence_ledger_and_storyline completed "
            f"| elapsed={self._elapsed(phase_start)} | evidence_points={len(evidence_ledger)} "
            f"| families={family_summary or 'none'}"
        )

        phase_start = time.monotonic()
        self._log("PHASE synthesis started | expected 60-180s")
        try:
            report = self._synthesize_web_report(display_topic, plan, chart_data_needs, sources, fact_pack, evidence_ledger, storyline_plan)
        except Exception as exc:
            (output_dir / "web_synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            report = self._fallback_report(display_topic, plan, sources, fact_pack, str(exc))
            self._log(f"PHASE synthesis fallback used | reason={str(exc)[:240]!r}")
        self._log(
            "PHASE synthesis completed "
            f"| elapsed={self._elapsed(phase_start)} | raw_keys={','.join(sorted(report.keys())[:20])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE evidence_exhibits started | expected <10s")
        evidence_exhibits = build_evidence_exhibits(display_topic, evidence_ledger, fact_pack, language=self.language)
        report = merge_evidence_exhibits(report, evidence_exhibits)
        self._log(
            "PHASE evidence_exhibits completed "
            f"| elapsed={self._elapsed(phase_start)} | exhibits={len(evidence_exhibits)} "
            f"| backed_by_ledger={sum(1 for exhibit in evidence_exhibits if exhibit.get('data_basis'))}"
        )

        phase_start = time.monotonic()
        self._log("PHASE normalize_and_validate_schema started | expected <10s")
        self._post_process(report, display_topic, sources, fact_pack)
        report = normalize_web_report(report, topic=display_topic, language=self.language)
        self._log(
            "PHASE normalize_and_validate_schema completed "
            f"| elapsed={self._elapsed(phase_start)} | takeaways={len(report.get('key_takeaways', []) or [])} "
            f"| sections={len(report.get('sections', []) or [])} | exhibits={len(report.get('exhibits', []) or [])} "
            f"| actions={len(report.get('action_steps', []) or [])} | references={len(report.get('references', []) or [])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE assets started | expected 60-360s when AI images are enabled")
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
        self._log(
            "PHASE assets completed "
            f"| elapsed={self._elapsed(phase_start)} | asset_keys={','.join(sorted(assets.keys()))}"
        )

        phase_start = time.monotonic()
        self._log("PHASE render_and_write started | expected <10s")
        html_path = render_web_report_html(report, assets, output_dir / "index.html", display_topic, self.language)
        markdown_path = render_web_report_markdown(report, output_dir / "report.md", display_topic, self.language)

        (output_dir / "web_report_payload.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "chart_data_needs.json").write_text(json.dumps(chart_data_needs, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "evidence_ledger.json").write_text(json.dumps(evidence_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "storyline_plan.json").write_text(json.dumps(storyline_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(
            "PHASE render_and_write completed "
            f"| elapsed={self._elapsed(phase_start)} | html={html_path} | markdown={markdown_path}"
        )
        self._log(f"END web report pipeline | total_elapsed={self._elapsed(run_start)}")

        return {
            "plan": plan,
            "chart_data_needs": chart_data_needs,
            "fact_pack": fact_pack.to_dict(),
            "evidence_ledger": evidence_ledger,
            "storyline_plan": storyline_plan,
            "sources": source_dicts,
            "report": report,
            "assets": assets,
            "output_dir": str(output_dir),
            "html_path": str(html_path),
            "markdown_path": str(markdown_path),
            "backup_dir": str(backup_dir),
        }

    def _log(self, message: str) -> None:
        print(f"[gen_rpt.web] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)

    @staticmethod
    def _elapsed(start: float) -> str:
        seconds = max(0, int(time.monotonic() - start))
        minutes, remainder = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m{remainder:02d}s"
        return f"{remainder}s"

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

    def _plan_chart_data_needs(self, topic: str, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        system = "You are a strategy-report exhibit architect. Return strict JSON only."
        if self.language == "zh":
            user = f"""
为 HTML thought-leadership 报告先定义图表数据需求，输出 JSON。

主题：{topic}
研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}

返回字段：chart_data_needs，数组 5 项。
每项包含：title、chart_type、executive_question、required_metrics、comparison_set、preferred_sources、search_queries、data_quality_rule。

要求：
- chart_type 必须从 bar、line、bubble、matrix、timeline 中选择。
- 覆盖投资/融资、市场规模或需求、产能/项目进展、成本/经济性、监管/采用门槛中的至少四类。
- required_metrics 必须写成可搜索、可量化的数据项，不要写“战略评分/优先级指数/成熟度指数”。
- search_queries 每项 2-3 条，优先官方、监管、协会、公司公告、PDF 报告和权威数据源。
- 图表只能基于真实公开数据；如果找不到数据，后续报告应把它作为证据缺口，而不是编造。
"""
        else:
            user = f"""
Define chart data needs before source collection for an HTML thought-leadership report. Return JSON only.

Topic: {topic}
Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Return field: chart_data_needs, an array of 5 items.
Each item must include: title, chart_type, executive_question, required_metrics, comparison_set, preferred_sources, search_queries, data_quality_rule.

Requirements:
- chart_type must be one of bar, line, bubble, matrix, timeline.
- Cover at least four of: investment/funding, market size or demand, capacity/project progress, cost/economics, regulation/adoption gates.
- required_metrics must be searchable quantitative data items. Do not request strategic scores, priority indexes, maturity indexes or other synthetic metrics.
- search_queries: 2-3 targeted queries per chart, prioritizing official sources, regulators, industry associations, company announcements, PDF reports and authoritative datasets.
- Charts may use only real public data. If a dataset cannot be found, the report should preserve it as an evidence gap rather than invent values.
"""
        payload = self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.05)
        return self._normalize_chart_data_needs(payload.get("chart_data_needs") or payload.get("needs") or payload.get("charts") or [])

    def _fallback_chart_data_needs(self, topic: str, plan: Dict[str, Any], reason: str) -> List[Dict[str, Any]]:
        base_queries = [str(query) for query in plan.get("search_queries", []) or [] if str(query).strip()]
        if self.language == "zh":
            title_prefix = topic
        else:
            title_prefix = topic
        needs = [
            {
                "title": f"{title_prefix}: investment and funding by year or company",
                "chart_type": "bar",
                "executive_question": "Where is capital actually flowing, and is the funding base deep enough for scale-up?",
                "required_metrics": ["funding amount", "investment year", "company or program name"],
                "comparison_set": ["companies", "programs", "years"],
                "preferred_sources": ["industry association report", "company announcement", "government program page", "PDF report"],
                "search_queries": [f"{topic} funding investment data report pdf", f"{topic} venture funding by company", f"{topic} government funding program amount"],
                "data_quality_rule": "Use named public amounts with dates and source URLs; do not convert them into priority scores.",
            },
            {
                "title": f"{title_prefix}: market size, demand or addressable use cases",
                "chart_type": "bar",
                "executive_question": "How large is the commercial prize, and which demand pools are credible enough to size?",
                "required_metrics": ["market size", "demand volume", "revenue or value pool", "forecast year"],
                "comparison_set": ["segments", "regions", "years"],
                "preferred_sources": ["government dataset", "industry association", "annual report", "market report"],
                "search_queries": [f"{topic} market size forecast data", f"{topic} demand outlook by segment", f"{topic} addressable market report pdf"],
                "data_quality_rule": "Keep forecast assumptions visible; exclude unsourced TAM claims.",
            },
            {
                "title": f"{title_prefix}: capacity, projects and commercialization milestones",
                "chart_type": "timeline",
                "executive_question": "Which projects have moved beyond claims into dated milestones?",
                "required_metrics": ["project name", "milestone date", "capacity or output metric", "status"],
                "comparison_set": ["projects", "technologies", "regions"],
                "preferred_sources": ["official project page", "regulatory filing", "company release", "government award page"],
                "search_queries": [f"{topic} project timeline capacity milestone", f"{topic} commercialization milestone official", f"{topic} pilot plant demonstration date"],
                "data_quality_rule": "Use dated public milestones; label claims that are company targets rather than achieved results.",
            },
            {
                "title": f"{title_prefix}: cost and economics benchmark",
                "chart_type": "bar",
                "executive_question": "What cost benchmark must the new option beat before it changes resource allocation?",
                "required_metrics": ["cost", "LCOE", "capex", "opex", "price benchmark"],
                "comparison_set": ["technologies", "incumbent alternatives", "years"],
                "preferred_sources": ["IEA", "Lazard", "NREL", "government reports", "company filings"],
                "search_queries": [f"{topic} cost benchmark LCOE data", f"{topic} capex cost estimate report", f"{topic} economics comparison incumbent alternatives"],
                "data_quality_rule": "Compare like units only and keep speculative costs out of the chart.",
            },
            {
                "title": f"{title_prefix}: regulatory and adoption gate map",
                "chart_type": "matrix",
                "executive_question": "Which nontechnical gates could delay adoption even if the technology works?",
                "required_metrics": ["regulatory status", "license date", "approval stage", "adoption barrier"],
                "comparison_set": ["countries", "regulators", "use cases"],
                "preferred_sources": ["regulator", "government policy page", "international organization", "standards body"],
                "search_queries": [f"{topic} regulation licensing framework", f"{topic} regulator approval rules", f"{topic} adoption barriers policy report"],
                "data_quality_rule": "Treat missing regulation as an explicit evidence gap; do not score it subjectively.",
            },
        ]
        if reason:
            needs[0]["fallback_reason"] = reason[:240]
        for need, query in zip(needs, base_queries):
            need.setdefault("search_queries", []).append(query)
        return self._normalize_chart_data_needs(needs)

    def _normalize_chart_data_needs(self, value: Any) -> List[Dict[str, Any]]:
        needs: List[Dict[str, Any]] = []
        for idx, item in enumerate(_as_list(value), start=1):
            if not isinstance(item, dict):
                continue
            chart_type = str(item.get("chart_type") or item.get("type") or "bar").lower().replace("_chart", "")
            if chart_type not in {"bar", "line", "bubble", "matrix", "timeline"}:
                chart_type = "bar"
            queries = [str(query).strip() for query in _as_list(item.get("search_queries") or item.get("queries")) if str(query).strip()]
            need = {
                "id": str(item.get("id") or f"chart-need-{idx}"),
                "title": str(item.get("title") or item.get("name") or f"Chart data need {idx}").strip(),
                "chart_type": chart_type,
                "executive_question": str(item.get("executive_question") or item.get("question") or "").strip(),
                "required_metrics": [str(x).strip() for x in _as_list(item.get("required_metrics") or item.get("metrics")) if str(x).strip()],
                "comparison_set": [str(x).strip() for x in _as_list(item.get("comparison_set") or item.get("comparisons")) if str(x).strip()],
                "preferred_sources": [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()],
                "search_queries": queries[:4],
                "data_quality_rule": str(item.get("data_quality_rule") or item.get("quality_rule") or "").strip(),
            }
            if need["title"] and need["search_queries"]:
                needs.append(need)
        return needs[:6]

    def _chart_need_queries(self, chart_data_needs: List[Dict[str, Any]]) -> List[str]:
        queries: List[str] = []
        for need in chart_data_needs:
            for query in need.get("search_queries", []) or []:
                clean = str(query or "").strip()
                if clean and clean not in queries:
                    queries.append(clean)
        return queries

    def _expanded_search_queries(self, plan: Dict[str, Any], chart_data_needs: List[Dict[str, Any]]) -> List[str]:
        plan_queries = [str(query).strip() for query in plan.get("search_queries", []) or [] if str(query).strip()]
        chart_queries = self._chart_need_queries(chart_data_needs)
        combined: List[str] = []
        for query in plan_queries[:3] + chart_queries + plan_queries[3:]:
            if query and query not in combined:
                combined.append(query)
        return combined

    def _synthesize_web_report(
        self,
        topic: str,
        plan: Dict[str, Any],
        chart_data_needs: List[Dict[str, Any]],
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
        evidence_ledger: List[Dict[str, Any]],
        storyline_plan: Dict[str, Any],
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
        evidence_text = json.dumps(evidence_ledger[:24], ensure_ascii=False, indent=2)
        system = "You are an elite strategy research author. Return one valid JSON object only. No markdown."
        if self.language == "zh":
            user = f"""
生成一份 HTML-first、类似 BCG publication 的深度分析网页报告数据结构，输出 JSON。

	主题：{topic}
	研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}
	图表数据需求（这些需求已用于定向检索）：
	{json.dumps(chart_data_needs, ensure_ascii=False, indent=2)}
	叙事主线计划：{json.dumps(storyline_plan, ensure_ascii=False, indent=2)}
事实包：{fact_pack.digest()}
证据台账（图表和数字判断只能来自这里或事实包）：
{evidence_text}
资料摘录：
{source_text}

必须包含字段：
title、dek、category、authors、intro、key_takeaways、sections、exhibits、action_steps、methodology、evidence_quality、references、disclaimer。

写作要求：
- 全程中文，面向 CEO/董事会/战略团队。
- 内容质量优先于长度；目标是 4-6 个扎实章节，而不是很多浅章节。
- title 和章节标题必须结论先行；不要用“概览、背景、趋势、分析、结论”这类标签标题。
- 必须顺着“叙事主线计划”展开，先回答核心管理问题，再用事实包和证据台账支撑判断。
- key_takeaways 3 条，每条必须有明确判断和管理含义。
- sections 4-6 个；每个包含 title、lead、paragraphs、evidence、so_what。每章 5-7 段，必须包含数字、日期、案例、机制或反例中的至少两类。
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects from the evidence ledger.
- 只能引用 Sources、事实包或证据台账里出现的来源；不要写“not in fact pack”“widely cited”或任何未抓取来源作为证据。
- exhibits 3-5 个；如果提出图表草稿，只能使用证据台账或事实包中的数字、年份、来源计数或同单位可比数据，必须保留 data_basis；不要使用方向性评分或内部综合指数。
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
	Chart data needs used for targeted source collection:
	{json.dumps(chart_data_needs, ensure_ascii=False, indent=2)}
	Storyline plan:
{json.dumps(storyline_plan, ensure_ascii=False, indent=2)}
Fact pack:
{fact_pack.digest()}
Evidence ledger (numeric claims and chart drafts may only use these entries or fact-pack counts):
{evidence_text}
Source excerpts:
{source_text}

Required fields:
title, dek, category, authors, intro, key_takeaways, sections, exhibits, action_steps, methodology, evidence_quality, references, disclaimer.

Writing rules:
- English only. Write for a CEO, board and strategy team audience.
- Depth matters more than page count. Aim for 4-6 substantial sections rather than many shallow chapters.
- The title and every section title must be conclusion-first. Avoid label headings such as Overview, Background, Trends, Analysis or Conclusion.
- Follow the storyline plan: answer the core management question first, then use the fact pack and evidence ledger to support the argument.
- key_takeaways: exactly 3, each with a clear claim and management implication.
- sections: 4-6 items. Each has title, lead, paragraphs, evidence, so_what. Each section needs 5-7 paragraphs and must include at least two of: numbers, dates, cases, causal mechanism, counter-evidence.
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects from the evidence ledger.
- Cite only sources present in Source excerpts, the fact pack or the evidence ledger. Never write "not in fact pack", "widely cited" or unsupported source names as evidence.
- exhibits: 3-5 items using metric_row, bar, line, matrix, process or bubble. If drafting exhibits, use only evidence-ledger values, years, source counts or same-unit comparable values from the fact pack, and include data_basis. Do not use directional scores, priority indexes, readiness indexes or internal synthesis values.
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
        seen_ref_urls = set()
        for item in report.get("references", []) or []:
            if isinstance(item, dict):
                url = str(item.get("url") or "")
                if url in real_urls and url not in seen_ref_urls:
                    refs.append(item)
                    seen_ref_urls.add(url)
        target_ref_count = min(8, len([source for source in sources if source.url]))
        for source in sources[:14]:
            if len(refs) >= target_ref_count:
                break
            if not source.url or source.url in seen_ref_urls:
                continue
            refs.append({"title": source.title or source.domain or source.url, "url": source.url, "note": source.snippet})
            seen_ref_urls.add(source.url)
        report["references"] = refs
        self._strengthen_thin_sections(report, topic, fact_pack)
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

    def _strengthen_thin_sections(self, report: Dict[str, Any], topic: str, fact_pack: ResearchFactPack) -> None:
        sections = report.get("sections") or []
        if not isinstance(sections, list):
            return
        facts = []
        for fact in fact_pack.numeric_facts + fact_pack.dated_facts + fact_pack.high_confidence_facts:
            cleaned_fact = _clean_fact_for_reader(fact)
            if cleaned_fact:
                facts.append(cleaned_fact)
        if not facts:
            facts = [
                (
                    f"The public source base contains {fact_pack.source_count} retained sources and "
                    f"{fact_pack.authoritative_source_count} authority-weighted sources for {topic}."
                )
            ]
        for idx, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                continue
            lead = str(section.get("lead") or "")
            paragraphs = [str(item).strip() for item in section.get("paragraphs") or [] if str(item).strip()]
            evidence = [_clean_fact_for_reader(str(item).strip()) for item in section.get("evidence") or [] if str(item).strip()]
            evidence = [item for item in evidence if item]
            title = str(section.get("title") or topic)
            fact = facts[(idx - 1) % len(facts)]
            while len(paragraphs) < 5:
                paragraphs.append(self._section_support_paragraph(topic, title, fact, len(paragraphs)))
            body_len = len(" ".join([lead] + paragraphs + evidence))
            fact_cursor = idx
            while body_len < 1550 and len(paragraphs) < 8:
                paragraphs.append(self._section_support_paragraph(topic, title, facts[fact_cursor % len(facts)], len(paragraphs)))
                body_len = len(" ".join([lead] + paragraphs + evidence))
                fact_cursor += 1
            if not evidence:
                evidence = facts[:3]
            body = " ".join([lead] + paragraphs + evidence)
            if not _has_numeric_cue(body):
                numeric_fact = next((item for item in facts if _has_numeric_cue(item)), facts[0] if facts else "")
                if numeric_fact and numeric_fact not in evidence:
                    evidence = [numeric_fact] + evidence
                if numeric_fact and len(paragraphs) < 8:
                    paragraphs.append(self._section_support_paragraph(topic, title, numeric_fact, len(paragraphs)))
            section["paragraphs"] = paragraphs[:8]
            section["evidence"] = evidence[:5]

    def _section_support_paragraph(self, topic: str, title: str, fact: str, position: int) -> str:
        if position % 2 == 0:
            return (
                f"The practical management test is whether this claim changes a real decision on {topic}: {fact} "
                "Leadership can translate that signal into explicit gates for capital allocation, partner selection, "
                "customer validation, operating readiness and the timing of the next commitment."
            )
        return (
            f"The evidence boundary also matters for {title}. {fact} "
            "Any stronger claim on market size, cost curve, ROI, schedule certainty or competitive advantage still needs "
            "primary validation before it becomes a board-level commitment."
        )

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


def _clean_fact_for_reader(fact: str) -> str:
    text = re.sub(r"^\[Source\s+\d+:[^\]]+\]\s*", "", str(fact or "")).strip()
    if re.match(r"^\s*\{['\"]id['\"]\s*:", text):
        fact_match = re.search(r"['\"]fact['\"]\s*:\s*['\"](.+?)(?<!\\)['\"]\s*,\s*['\"](?:value|display_value|year|unit|metric_family)['\"]", text, re.S)
        display_match = re.search(r"['\"]display_value['\"]\s*:\s*['\"](.+?)(?<!\\)['\"]", text, re.S)
        extracted = fact_match.group(1) if fact_match else ""
        display = display_match.group(1) if display_match else ""
        if extracted:
            text = f"{display}: {extracted}" if display and display not in extracted else extracted
    text = re.sub(r"\s+", " ", text).strip(" .")
    if _reader_noise(text):
        return ""
    return text


def _has_numeric_cue(text: str) -> bool:
    return bool(
        re.search(
            r"\b(19|20)\d{2}\b|\b\d+(?:\.\d+)?%|\b\$\d+|\b\d+(?:\.\d+)?\s*(?:billion|million|trillion|GW|MW|MJ|kg|years?|months?)\b",
            str(text or ""),
            re.I,
        )
    )


def _reader_noise(text: str) -> bool:
    lower = str(text or "").lower()
    if not lower:
        return True
    return any(
        token in lower
        for token in (
            "making it work advantages of fusion",
            "fusion glossary",
            "all news",
            "photos videos",
            "subscribe to the newsletter",
            "select your newsletters",
            "page body could not be fully extracted",
            "not in fact pack",
            "widely cited",
        )
    )


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
