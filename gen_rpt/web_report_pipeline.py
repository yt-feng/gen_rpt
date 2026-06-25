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
from .web_publication_contract import client_visible_internal_hits, publication_contract_prompt
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
        self._log("ETA planning=15-90s, chart_data_needs=10-60s, source_collection=60-300s, evidence=5-15s, synthesis=60-180s, visuals=60-360s")

        phase_start = time.monotonic()
        self._log("PHASE planning started | expected 15-90s")
        try:
            plan = self._plan_research(display_topic)
        except Exception as exc:
            (output_dir / "web_plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(display_topic, str(exc))
            self._log(f"PHASE planning fallback used | reason={str(exc)[:240]!r}")
        plan = self._normalize_research_plan(plan, display_topic)
        self._log(
            "PHASE planning completed "
            f"| elapsed={self._elapsed(phase_start)} | queries={len(plan.get('search_queries', []) or [])} "
            f"| outline={len(plan.get('outline', []) or [])} "
            f"| hypotheses={len(plan.get('hypotheses', []) or [])} "
            f"| sizing_methods={len(plan.get('market_sizing_plan', {}).get('methods', []) or [])}"
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
        max_sources = int(os.getenv("GEN_RPT_MAX_SOURCES", "28"))
        max_queries = int(os.getenv("GEN_RPT_MAX_QUERIES", "18"))
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
        evidence_exhibits = build_evidence_exhibits(display_topic, evidence_ledger, fact_pack, plan=plan, chart_data_needs=chart_data_needs, language=self.language)
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
        visible_hits = client_visible_internal_hits(self._client_visible_text(report))
        if visible_hits:
            self._log("PHASE publication_contract warning | visible_internal_language=" + ",".join(visible_hits[:6]))
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
        (output_dir / "analysis_framework.json").write_text(json.dumps(self._analysis_framework(plan, chart_data_needs, storyline_plan), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "publication_contract.json").write_text(json.dumps(self._publication_contract_metadata(), ensure_ascii=False, indent=2), encoding="utf-8")
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
            "analysis_framework": self._analysis_framework(plan, chart_data_needs, storyline_plan),
            "publication_contract": self._publication_contract_metadata(),
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
        system = "You are a senior research planner for a BlueOcean-style digital publication. Return strict JSON only."
        if self.language == "zh":
            user = f"""
为一个 HTML-first 深度分析网页生成研究计划，输出 JSON。

主题：{topic}

必须包含字段：objective、audience、decision_question、issue_tree、hypotheses、market_sizing_plan、validation_data_needs、search_queries、source_strategy、outline、exhibit_ideas、risks。
要求：
- hypotheses 5-7 条，每条包含 id、hypothesis、decision_relevance、needed_evidence、search_queries。必须是可证伪、可找数据验证的商业假设。
- market_sizing_plan 必须包含 methods 数组，覆盖 top-down、bottom-up、adoption funnel、value pool 或 supply-side sizing 中至少 3 种；每种方法包含 formula、variables、preferred_sources、search_queries、known_limitations。
- validation_data_needs 8-12 条，列出市场规模、需求代理、客户/用户数、价格/ARPU/ASP、成本、产能/供给、融资、政策、竞争份额、案例或时间线等可检索数据。
- search_queries 12-16 条，优先能找到政府、监管、公司公告、年报、行业协会、国际组织、权威媒体、学术或咨询机构资料；其中至少 6 条直接服务于 market sizing 或假设验证。
- outline 4-6 个章节，标题必须是结论先行。
- exhibit_ideas 3-5 个，不要装饰图；每个图都要回答一个管理层问题。
- 明确哪些信息需要数字、案例、时间线或反例来验证。
"""
        else:
            user = f"""
Create a research plan for an HTML-first deep analysis article and return JSON only.

Topic: {topic}

Required fields: objective, audience, decision_question, issue_tree, hypotheses, market_sizing_plan, validation_data_needs, search_queries, source_strategy, outline, exhibit_ideas, risks.
Requirements:
- 5-7 hypotheses. Each must include id, hypothesis, decision_relevance, needed_evidence and search_queries. They must be falsifiable commercial hypotheses that can be tested with public data.
- market_sizing_plan must include a methods array covering at least three of: top-down, bottom-up, adoption funnel, value pool and supply-side sizing. Each method needs formula, variables, preferred_sources, search_queries and known_limitations.
- validation_data_needs: 8-12 searchable data needs covering market size, demand proxies, customers/users, price/ARPU/ASP, cost, capacity/supply, funding, policy, competitive share, cases or timeline proof.
- 12-16 public-web search queries, prioritizing government, regulators, company filings, annual reports, industry associations, international organizations, authoritative media, academic sources and consulting research. At least six queries should directly support market sizing or hypothesis testing.
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

返回字段：chart_data_needs，数组 7 项。
每项包含：title、chart_type、executive_question、required_metrics、comparison_set、preferred_sources、search_queries、data_quality_rule。

要求：
- chart_type 必须从 bar、line、bubble、matrix、timeline 中选择。
- 覆盖 top-down sizing、bottom-up sizing、adoption funnel、价值池/ROI、投资/融资、产能/项目进展、成本/经济性、监管/采用门槛中的至少六类，但输出必须是要寻找的真实指标和来源，不是测算方法图。
- required_metrics 必须写成可搜索、可量化的数据项，不要写“战略评分/优先级指数/成熟度指数”。
- search_queries 每项 2-3 条，优先官方、监管、协会、公司公告、PDF 报告和权威数据源。
- 图表只能基于真实公开数据；如果找不到数据，后续报告应把它写成仍需证明的商业问题，而不是编造或展示后台验证清单。
"""
        else:
            user = f"""
Define chart data needs before source collection for an HTML thought-leadership report. Return JSON only.

Topic: {topic}
Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Return field: chart_data_needs, an array of 7 items.
Each item must include: title, chart_type, executive_question, required_metrics, comparison_set, preferred_sources, search_queries, data_quality_rule.

Requirements:
- chart_type must be one of bar, line, bubble, matrix, timeline.
- Cover at least six of: top-down sizing, bottom-up sizing, adoption funnel, value pool/ROI, investment/funding, capacity/project progress, cost/economics, regulation/adoption gates, but output real metrics and sources to search for, not a sizing-method exhibit.
- required_metrics must be searchable quantitative data items. Do not request strategic scores, priority indexes, maturity indexes or other synthetic metrics.
- search_queries: 2-3 targeted queries per chart, prioritizing official sources, regulators, industry associations, company announcements, PDF reports and authoritative datasets.
- Charts may use only real public data. If a dataset cannot be found, the report should frame it as a business question still needing proof rather than invent values or display a backstage validation checklist.
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
                "title": f"{title_prefix}: top-down market ceiling and addressable demand pool",
                "chart_type": "bar",
                "executive_question": "What is the largest credible demand pool before adoption, pricing and eligibility filters?",
                "required_metrics": ["total market size", "category spend or demand volume", "segment share", "forecast year"],
                "comparison_set": ["regions", "segments", "years"],
                "preferred_sources": ["government dataset", "industry association", "annual report", "international organization", "market report"],
                "search_queries": [f"{topic} total addressable market data", f"{topic} market size by segment forecast", f"{topic} demand volume official data"],
                "data_quality_rule": "Use source-stated market or demand values and keep forecast years and assumptions visible.",
                "sizing_role": "top_down_market_ceiling",
            },
            {
                "title": f"{title_prefix}: bottom-up buyer count, usage and pricing inputs",
                "chart_type": "matrix",
                "executive_question": "Which customer, unit-volume and price inputs can turn market narrative into a revenue bridge?",
                "required_metrics": ["customer count", "unit demand", "usage frequency", "price", "ARPU", "ASP"],
                "comparison_set": ["customer segments", "use cases", "regions"],
                "preferred_sources": ["company filings", "industry association", "regulator dataset", "survey", "case study"],
                "search_queries": [f"{topic} customer count price ARPU data", f"{topic} adoption rate users units sold", f"{topic} average selling price demand by customer segment"],
                "data_quality_rule": "Do not combine buyer count, usage and price unless all units are explicit and comparable.",
                "sizing_role": "bottom_up_revenue_bridge",
            },
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
                "sizing_role": "demand_pool_cross_check",
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
                "sizing_role": "supply_side_constraint",
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
                "sizing_role": "unit_economics_gate",
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
                "sizing_role": "adoption_gate",
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
                "sizing_role": str(item.get("sizing_role") or item.get("market_sizing_role") or "").strip(),
                "hypothesis_ids": [str(x).strip() for x in _as_list(item.get("hypothesis_ids") or item.get("hypotheses")) if str(x).strip()],
            }
            if need["title"] and need["search_queries"]:
                needs.append(need)
        return needs[:8]

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
        framework_queries = self._analysis_framework_queries(plan)
        combined: List[str] = []
        for query in plan_queries[:4] + chart_queries + framework_queries + plan_queries[4:]:
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
        contract_text = publication_contract_prompt(self.language)
        system = "You are an elite strategy research author. Return one valid JSON object only. No markdown."
        if self.language == "zh":
            user = f"""
生成一份 HTML-first、类似 BlueOcean publication 的深度分析网页报告数据结构，输出 JSON。

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

客户可见合同（必须遵守）：
{contract_text}

必须包含字段：
title、dek、category、authors、intro、key_takeaways、sections、exhibits、action_steps、methodology、evidence_quality、references、disclaimer。

写作要求：
- 全程中文，面向 CEO/董事会/战略团队。
- 内容质量优先于长度；目标是 4-6 个扎实章节，而不是很多浅章节。
- title 和章节标题必须结论先行；不要用“概览、背景、趋势、分析、结论”这类标签标题。
- 叙事必须像一篇成熟咨询 publication：前台只呈现结论、案例、数字、机制、反例和管理含义；后台思考工具不得露出。
- 可以在内部使用假设验证和市场机会测算来组织证据，但客户可见字段不得出现 hypothesis、假设验证、market sizing、sizing bridge、TAM、SAM、SOM、issue tree、fact pack、evidence ledger、storyline plan、validation task、source boundary、data basis 等方法名或工作台语言。
- exhibits 仍必须保留 JSON 键 data_basis 以便机器可追溯；但 title、subtitle、caption、source_note、paragraphs、methodology 等可见文案不得写 “data basis”。
- 每个关键判断要能被事实包、证据台账或来源摘录支撑；缺失变量要自然写成“还需要验证的商业问题”，不要写成框架步骤。
- key_takeaways 3 条，每条必须有明确判断和管理含义。
- sections 4-6 个；每个包含 title、lead、paragraphs、evidence、so_what。每章 5-7 段，必须包含数字、日期、案例、机制或反例中的至少两类。
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects or internal evidence-log language.
- 只能引用 Sources、事实包或证据台账里出现的来源；不要使用内部事实包缺失措辞、泛泛引用热度表述或任何未抓取来源作为证据。
- exhibits 3-6 个；如果提出图表草稿，只能使用证据台账或事实包中的数字、年份、来源计数或同单位可比数据，必须保留 data_basis；不要展示机会测算、假设验证、验证任务表、工作清单或框架步骤；不要使用方向性评分或内部综合指数。
- market_sizing_plan 和 chart_data_needs 只用于指导检索更多可验证数据，不能变成 exhibit、正文小节或 methodology。
- action_steps 3-5 个，每个包含 horizon、action、success_metric。
- references 只能使用上方 Sources 中真实 URL。
- 不要暴露内部提示、不要说“本章节认为/本报告认为/本分析基于结构化研究计划/假设 H1 得到支持”，直接写判断。
- methodology 只写公开来源和独立核验边界，不解释研究框架、假设数量、证据台账或市场测算方法。
- 缺失的市场规模、份额、ROI、成本等不要编造，写成证据缺口和核验任务。
"""
        else:
            user = f"""
Generate an HTML-first, BlueOcean-publication-like deep analysis report data structure and return JSON.

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

Client-visible publication contract:
{contract_text}

Required fields:
title, dek, category, authors, intro, key_takeaways, sections, exhibits, action_steps, methodology, evidence_quality, references, disclaimer.

Writing rules:
- English only. Write for a CEO, board and strategy team audience.
- Depth matters more than page count. Aim for 4-6 substantial sections rather than many shallow chapters.
- The title and every section title must be conclusion-first. Avoid label headings such as Overview, Background, Trends, Analysis or Conclusion.
- Write like a mature strategy publication: the reader should see conclusions, examples, numbers, causal mechanisms, counter-evidence and management implications, not the author's backstage workbench.
- You may use hypothesis testing and opportunity-sizing logic internally, but client-visible fields must not contain the words or labels hypothesis, hypotheses, hypothesis-driven, market sizing, sizing bridge, TAM, SAM, SOM, issue tree, fact pack, evidence ledger, storyline plan, validation task, source boundary or data basis.
- Exhibits must still keep the JSON key data_basis for machine traceability; do not write the phrase "data basis" in title, subtitle, caption, source_note, paragraphs, methodology or other visible prose.
- Every material claim must be supportable by the source excerpts, fact pack or evidence ledger; missing variables should read as business questions that still need proof, not as framework steps.
- key_takeaways: exactly 3, each with a clear claim and management implication.
- sections: 4-6 items. Each has title, lead, paragraphs, evidence, so_what. Each section needs 5-7 paragraphs and must include at least two of: numbers, dates, cases, causal mechanism, counter-evidence.
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects or internal evidence-log language.
- Cite only sources present in Source excerpts, the fact pack or the evidence ledger. Never use internal fact-pack gap phrasing, generic popularity claims or unsupported source names as evidence.
- exhibits: 3-6 items using metric_row, bar, line, timeline or bubble. Use matrix only when it contains source-observed facts rather than a workplan. If drafting exhibits, use only evidence-ledger values, years, source counts or same-unit comparable values from the fact pack, and include data_basis. Do not show opportunity sizing, hypothesis testing, validation-task tables, workplans or framework steps. Do not use directional scores, priority indexes, readiness indexes or internal synthesis values.
- market_sizing_plan and chart_data_needs exist only to guide collection of more verifiable data; they must not become exhibits, body sections or methodology.
- action_steps: 3-5 items, each with horizon, action, success_metric.
- references may only use real URLs present in Sources.
- Do not expose internal prompt language. Do not write "this section argues", "this report finds", "Hypothesis H1 is supported", or "this analysis is based on a structured research plan"; state the insight directly.
- methodology should only describe public sources and independent-validation boundaries; do not explain the research framework, number of hypotheses, evidence ledger or sizing methods.
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

    def _normalize_research_plan(self, plan: Dict[str, Any], topic: str) -> Dict[str, Any]:
        normalized = dict(plan or {})
        normalized["search_queries"] = self._dedupe_texts(_as_list(normalized.get("search_queries")))[:18]
        normalized["hypotheses"] = self._normalize_hypotheses(normalized.get("hypotheses"), topic)
        normalized["market_sizing_plan"] = self._normalize_market_sizing_plan(normalized.get("market_sizing_plan"), topic)
        normalized["validation_data_needs"] = self._normalize_validation_data_needs(normalized.get("validation_data_needs"), topic)

        for query in self._analysis_framework_queries(normalized):
            if query not in normalized["search_queries"]:
                normalized["search_queries"].append(query)
            if len(normalized["search_queries"]) >= 18:
                break
        return normalized

    def _normalize_hypotheses(self, value: Any, topic: str) -> List[Dict[str, Any]]:
        items = []
        for idx, item in enumerate(_as_list(value), start=1):
            if isinstance(item, dict):
                hypothesis = str(item.get("hypothesis") or item.get("claim") or item.get("title") or "").strip()
                needed = [str(x).strip() for x in _as_list(item.get("needed_evidence") or item.get("evidence_needed") or item.get("data_needed")) if str(x).strip()]
                queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
                decision_relevance = str(item.get("decision_relevance") or item.get("why_it_matters") or item.get("management_relevance") or "").strip()
                item_id = str(item.get("id") or f"H{idx}").strip()
            else:
                hypothesis = str(item or "").strip()
                needed = []
                queries = []
                decision_relevance = ""
                item_id = f"H{idx}"
            if not hypothesis:
                continue
            items.append(
                {
                    "id": item_id or f"H{idx}",
                    "hypothesis": hypothesis,
                    "decision_relevance": decision_relevance,
                    "needed_evidence": needed[:6],
                    "search_queries": queries[:4],
                }
            )
        if len(items) < 5:
            defaults = self._default_hypotheses(topic)
            seen = {self._norm_key(item["hypothesis"]) for item in items}
            for item in defaults:
                if self._norm_key(item["hypothesis"]) not in seen:
                    items.append(item)
                    seen.add(self._norm_key(item["hypothesis"]))
                if len(items) >= 6:
                    break
        return items[:7]

    def _normalize_market_sizing_plan(self, value: Any, topic: str) -> Dict[str, Any]:
        raw = dict(value or {}) if isinstance(value, dict) else {}
        methods = []
        method_source = raw.get("methods") or raw.get("approaches") or ([] if isinstance(value, dict) else value)
        for idx, item in enumerate(_as_list(method_source), start=1):
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or item.get("name") or item.get("type") or f"Method {idx}").strip()
            formula = str(item.get("formula") or item.get("calculation") or "").strip()
            variables = [str(x).strip() for x in _as_list(item.get("variables") or item.get("inputs")) if str(x).strip()]
            sources = [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()]
            queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
            limitations = [str(x).strip() for x in _as_list(item.get("known_limitations") or item.get("limitations")) if str(x).strip()]
            if method:
                methods.append(
                    {
                        "method": method,
                        "formula": formula,
                        "variables": variables[:8],
                        "preferred_sources": sources[:6],
                        "search_queries": queries[:4],
                        "known_limitations": limitations[:4],
                    }
                )
        if len(methods) < 3:
            defaults = self._default_market_sizing_plan(topic)["methods"]
            seen = {self._norm_key(item["method"]) for item in methods}
            for item in defaults:
                if self._norm_key(item["method"]) not in seen:
                    methods.append(item)
                    seen.add(self._norm_key(item["method"]))
                if len(methods) >= 5:
                    break
        default_plan = self._default_market_sizing_plan(topic)
        return {
            "sizing_question": str(raw.get("sizing_question") or raw.get("question") or default_plan["sizing_question"]).strip(),
            "methods": methods[:5],
            "evidence_rule": str(raw.get("evidence_rule") or "Use public source values when available; keep missing variables as validation tasks.").strip(),
        }

    def _normalize_validation_data_needs(self, value: Any, topic: str) -> List[Dict[str, Any]]:
        needs = []
        for idx, item in enumerate(_as_list(value), start=1):
            if isinstance(item, dict):
                metric = str(item.get("metric") or item.get("data") or item.get("name") or item.get("title") or "").strip()
                reason = str(item.get("decision_use") or item.get("reason") or item.get("why_needed") or "").strip()
                sources = [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()]
                queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
            else:
                metric = str(item or "").strip()
                reason = ""
                sources = []
                queries = []
            if metric:
                needs.append({"id": f"D{idx}", "metric": metric, "decision_use": reason, "preferred_sources": sources[:5], "search_queries": queries[:4]})
        if len(needs) < 8:
            defaults = self._default_validation_data_needs(topic)
            seen = {self._norm_key(item["metric"]) for item in needs}
            for item in defaults:
                if self._norm_key(item["metric"]) not in seen:
                    needs.append(item)
                    seen.add(self._norm_key(item["metric"]))
                if len(needs) >= 12:
                    break
        return needs[:12]

    def _analysis_framework_queries(self, plan: Dict[str, Any]) -> List[str]:
        queries: List[str] = []
        for hypothesis in plan.get("hypotheses", []) or []:
            if isinstance(hypothesis, dict):
                queries.extend(str(x).strip() for x in _as_list(hypothesis.get("search_queries")) if str(x).strip())
        sizing_plan = plan.get("market_sizing_plan") or {}
        if isinstance(sizing_plan, dict):
            for method in sizing_plan.get("methods", []) or []:
                if isinstance(method, dict):
                    queries.extend(str(x).strip() for x in _as_list(method.get("search_queries")) if str(x).strip())
        for need in plan.get("validation_data_needs", []) or []:
            if isinstance(need, dict):
                queries.extend(str(x).strip() for x in _as_list(need.get("search_queries")) if str(x).strip())
        return self._dedupe_texts(queries)

    def _analysis_framework(self, plan: Dict[str, Any], chart_data_needs: List[Dict[str, Any]], storyline_plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision_question": plan.get("decision_question"),
            "hypotheses": plan.get("hypotheses", []),
            "market_sizing_plan": plan.get("market_sizing_plan", {}),
            "validation_data_needs": plan.get("validation_data_needs", []),
            "chart_data_needs": chart_data_needs,
            "storyline_plan": storyline_plan,
        }

    def _publication_contract_metadata(self) -> Dict[str, Any]:
        return {
            "root_cause": "DeepSeek is treated as a diligent low-agency worker. It can collect data and draft structured text, but it is not trusted to decide what belongs in client-visible prose.",
            "architecture": [
                "Backstage research workbench: hypotheses, opportunity sizing, data needs, source collection and evidence extraction.",
                "Publication synthesis: conclusions, examples, numbers, mechanisms, counter-evidence and management implications only.",
                "Deterministic renderer guard: rewrite or remove workbench language that leaks into visible prose.",
                "Audit gate: fail generated HTML if client-visible internal language remains.",
            ],
            "client_visible_contract": publication_contract_prompt(self.language),
        }

    @staticmethod
    def _client_visible_text(report: Dict[str, Any]) -> str:
        visible = {
            "title": report.get("title"),
            "dek": report.get("dek"),
            "intro": report.get("intro"),
            "key_takeaways": report.get("key_takeaways"),
            "sections": report.get("sections"),
            "exhibits": [
                {
                    "title": exhibit.get("title"),
                    "subtitle": exhibit.get("subtitle"),
                    "caption": exhibit.get("caption"),
                    "source_note": exhibit.get("source_note"),
                    "rows": exhibit.get("rows"),
                    "columns": exhibit.get("columns"),
                    "values": exhibit.get("values"),
                    "categories": exhibit.get("categories"),
                    "series": exhibit.get("series"),
                    "points": exhibit.get("points"),
                }
                for exhibit in report.get("exhibits", []) or []
                if isinstance(exhibit, dict)
            ],
            "action_steps": report.get("action_steps"),
            "methodology": report.get("methodology"),
        }
        return json.dumps(visible, ensure_ascii=False)

    def _default_hypotheses(self, topic: str) -> List[Dict[str, Any]]:
        if self.language == "zh":
            return [
                {"id": "H1", "hypothesis": f"{topic} 的需求池足够大，值得进入资源配置讨论。", "decision_relevance": "决定是否需要做完整市场规模测算。", "needed_evidence": ["市场规模", "需求量", "分地区或分场景需求"], "search_queries": [f"{topic} 市场规模 数据", f"{topic} 需求 预测 分场景"]},
                {"id": "H2", "hypothesis": "客户采用和付费意愿已经出现可验证信号。", "decision_relevance": "决定是否进入客户验证和销售资源投入。", "needed_evidence": ["客户数量", "采用率", "订单", "价格或 ARPU"], "search_queries": [f"{topic} 客户 采用率 价格", f"{topic} 订单 客户 案例"]},
                {"id": "H3", "hypothesis": "供给、产能或交付能力不会成为短期增长瓶颈。", "decision_relevance": "决定增长判断是否需要被供应侧约束下调。", "needed_evidence": ["产能", "项目", "供应链", "交付周期"], "search_queries": [f"{topic} 产能 项目 供应链", f"{topic} 公司 公告 产能"]},
                {"id": "H4", "hypothesis": "单位经济性或成本曲线已经接近可商业化区间。", "decision_relevance": "决定机会是预算项、试点项还是观察项。", "needed_evidence": ["成本", "价格", "毛利率", "CAPEX", "OPEX"], "search_queries": [f"{topic} 成本 价格 毛利率", f"{topic} CAPEX OPEX 经济性"]},
                {"id": "H5", "hypothesis": "领先者存在可防守的竞争优势，而不是短期窗口。", "decision_relevance": "决定是否押注特定公司、伙伴或能力。", "needed_evidence": ["市场份额", "专利", "客户案例", "融资", "渠道"], "search_queries": [f"{topic} 竞争格局 市场份额", f"{topic} 领先企业 融资 专利 客户"]},
                {"id": "H6", "hypothesis": "政策、监管或标准的时间表支持商业化节奏。", "decision_relevance": "决定进入节奏和风险缓冲。", "needed_evidence": ["政策日期", "监管许可", "标准", "补贴或采购"], "search_queries": [f"{topic} 政策 监管 标准", f"{topic} 补贴 许可 时间表"]},
            ]
        return [
            {"id": "H1", "hypothesis": f"The demand pool for {topic} is large enough to deserve resource-allocation debate.", "decision_relevance": "Determines whether a full sizing exercise is warranted.", "needed_evidence": ["market size", "demand volume", "regional or segment demand"], "search_queries": [f"{topic} market size data", f"{topic} demand forecast by segment"]},
            {"id": "H2", "hypothesis": "Customer adoption and willingness to pay are visible in public evidence.", "decision_relevance": "Determines whether to fund customer validation and sales work.", "needed_evidence": ["customer count", "adoption rate", "orders", "price or ARPU"], "search_queries": [f"{topic} customer adoption price data", f"{topic} orders customer case study"]},
            {"id": "H3", "hypothesis": "Supply, capacity or delivery capability will not bottleneck near-term growth.", "decision_relevance": "Determines whether the growth case needs a supply-side haircut.", "needed_evidence": ["capacity", "projects", "supply chain", "delivery timeline"], "search_queries": [f"{topic} capacity projects supply chain", f"{topic} company announcement capacity"]},
            {"id": "H4", "hypothesis": "Unit economics or the cost curve is close enough to commercial range.", "decision_relevance": "Determines whether the opportunity is a budget item, pilot or watchlist topic.", "needed_evidence": ["cost", "price", "gross margin", "CAPEX", "OPEX"], "search_queries": [f"{topic} cost price margin", f"{topic} CAPEX OPEX economics"]},
            {"id": "H5", "hypothesis": "Leading players have defensible advantage rather than a temporary window.", "decision_relevance": "Determines whether to back a company, partner or capability.", "needed_evidence": ["market share", "patents", "customer references", "funding", "channels"], "search_queries": [f"{topic} competitive landscape market share", f"{topic} leading companies funding patents customers"]},
            {"id": "H6", "hypothesis": "Policy, regulatory or standards timing supports commercialization.", "decision_relevance": "Determines entry timing and risk buffers.", "needed_evidence": ["policy date", "regulatory license", "standard", "subsidy or procurement"], "search_queries": [f"{topic} regulation policy standard", f"{topic} subsidy license timeline"]},
        ]

    def _default_market_sizing_plan(self, topic: str) -> Dict[str, Any]:
        if self.language == "zh":
            return {
                "sizing_question": f"{topic} 的可信市场空间有多大，哪些变量仍需核验？",
                "methods": [
                    {"method": "Top-down sizing", "formula": "总需求池 x 可服务细分占比 x 可触达地域/场景占比", "variables": ["总市场规模", "细分市场占比", "地域或场景可服务比例"], "preferred_sources": ["政府数据", "行业协会", "国际组织", "咨询报告"], "search_queries": [f"{topic} 总市场规模 细分 占比", f"{topic} demand by segment official"], "known_limitations": ["高层市场规模容易包含不可服务需求"]},
                    {"method": "Bottom-up sizing", "formula": "潜在客户数 x 单客户用量 x 单价/ARPU x 采用率", "variables": ["客户数", "单客户用量", "价格或 ARPU", "采用率"], "preferred_sources": ["公司公告", "年报", "行业调查", "监管数据"], "search_queries": [f"{topic} 客户数量 单价 ARPU", f"{topic} adoption rate units price"], "known_limitations": ["客户和价格口径必须一致"]},
                    {"method": "Adoption funnel sizing", "formula": "目标客户池 x 试点率 x 转化率 x 扩张率", "variables": ["目标客户池", "试点数量", "转化率", "扩张率"], "preferred_sources": ["客户案例", "订单公告", "招投标", "渠道数据"], "search_queries": [f"{topic} pilot customer conversion", f"{topic} 招标 订单 客户"], "known_limitations": ["早期案例可能不能代表大规模采用"]},
                    {"method": "Value pool sizing", "formula": "客户成本/收入池 x 可改善比例 x 供应商价值捕获率", "variables": ["客户成本池", "改善比例", "价值捕获率"], "preferred_sources": ["客户财报", "行业成本基准", "案例研究"], "search_queries": [f"{topic} ROI cost savings case study", f"{topic} value pool customer cost"], "known_limitations": ["价值捕获率通常需要一手访谈验证"]},
                    {"method": "Supply-side sizing", "formula": "可用产能 x 利用率 x 单位价格", "variables": ["产能", "利用率", "单位价格"], "preferred_sources": ["产能公告", "项目备案", "公司年报"], "search_queries": [f"{topic} 产能 利用率 单价", f"{topic} project capacity announcement"], "known_limitations": ["公告产能不等于可交付产能"]},
                ],
            }
        return {
            "sizing_question": f"How large is the credible opportunity for {topic}, and which variables still need validation?",
            "methods": [
                {"method": "Top-down sizing", "formula": "Total demand pool x serviceable segment share x reachable geography/use-case share", "variables": ["total market size", "segment share", "reachable geography or use-case share"], "preferred_sources": ["government data", "industry association", "international organization", "consulting report"], "search_queries": [f"{topic} total market size segment share", f"{topic} demand by segment official data"], "known_limitations": ["High-level market size can include demand that is not serviceable."]},
                {"method": "Bottom-up sizing", "formula": "Potential customers x usage per customer x price/ARPU x adoption rate", "variables": ["customer count", "usage per customer", "price or ARPU", "adoption rate"], "preferred_sources": ["company announcements", "annual reports", "industry surveys", "regulatory datasets"], "search_queries": [f"{topic} customer count price ARPU", f"{topic} adoption rate units price"], "known_limitations": ["Customer and price definitions must use the same scope."]},
                {"method": "Adoption funnel sizing", "formula": "Target customer pool x pilot rate x conversion rate x expansion rate", "variables": ["target customer pool", "pilot count", "conversion rate", "expansion rate"], "preferred_sources": ["customer cases", "order announcements", "procurement data", "channel data"], "search_queries": [f"{topic} pilot customer conversion", f"{topic} orders customers adoption"], "known_limitations": ["Early case studies may not represent scaled adoption."]},
                {"method": "Value pool sizing", "formula": "Customer cost/revenue pool x improvement rate x supplier value-capture rate", "variables": ["customer cost pool", "improvement rate", "value-capture rate"], "preferred_sources": ["customer filings", "industry cost benchmarks", "case studies"], "search_queries": [f"{topic} ROI cost savings case study", f"{topic} value pool customer cost"], "known_limitations": ["Value-capture rate usually needs primary research."]},
                {"method": "Supply-side sizing", "formula": "Available capacity x utilization x unit price", "variables": ["capacity", "utilization", "unit price"], "preferred_sources": ["capacity announcements", "project filings", "company annual reports"], "search_queries": [f"{topic} capacity utilization unit price", f"{topic} project capacity announcement"], "known_limitations": ["Announced capacity is not the same as deliverable capacity."]},
            ],
        }

    def _default_validation_data_needs(self, topic: str) -> List[Dict[str, Any]]:
        labels = [
            ("Market size", "Quantify TAM/SAM before writing a growth claim.", [f"{topic} market size forecast data"]),
            ("Demand volume", "Cross-check revenue pools with physical or usage demand.", [f"{topic} demand volume official data"]),
            ("Customer or user count", "Build a bottom-up buyer base.", [f"{topic} customer count users"]),
            ("Adoption or penetration rate", "Estimate realistic conversion from demand to revenue.", [f"{topic} adoption rate penetration"]),
            ("Price, ARPU or ASP", "Turn demand units into revenue.", [f"{topic} price ARPU ASP"]),
            ("Cost, CAPEX or OPEX", "Test whether the opportunity can earn attractive economics.", [f"{topic} cost CAPEX OPEX"]),
            ("Capacity or project pipeline", "Check whether supply can serve demand.", [f"{topic} capacity project pipeline"]),
            ("Funding or investment", "Validate capital formation and investor conviction.", [f"{topic} funding investment by company"]),
            ("Regulatory or policy gate", "Identify non-market blockers to adoption.", [f"{topic} regulation policy approval"]),
            ("Competitive share", "Separate market growth from player-level advantage.", [f"{topic} market share competitive landscape"]),
            ("Case studies or orders", "Verify that adoption exists outside narrative claims.", [f"{topic} customer case order announcement"]),
            ("Milestone timeline", "Anchor the commercialization clock in dated evidence.", [f"{topic} milestone timeline commercialization"]),
        ]
        if self.language == "zh":
            translations = [
                ("市场规模", "量化 TAM/SAM，避免把增长叙事直接写成规模判断。"),
                ("需求量", "用实物量或使用量交叉验证收入池。"),
                ("客户或用户数", "建立 bottom-up 买方基数。"),
                ("采用率或渗透率", "估计需求向收入转化的现实速度。"),
                ("价格、ARPU 或 ASP", "把需求单位转成收入。"),
                ("成本、CAPEX 或 OPEX", "判断机会是否具备经济性。"),
                ("产能或项目管线", "验证供给是否能服务需求。"),
                ("融资或投资", "验证资本形成和投资者信心。"),
                ("监管或政策门槛", "识别非市场采用阻碍。"),
                ("竞争份额", "区分市场增长和单一玩家优势。"),
                ("客户案例或订单", "验证采用是否存在于叙事之外。"),
                ("里程碑时间线", "用有日期的证据锚定商业化节奏。"),
            ]
            labels = [(metric, reason, queries) for (metric, reason), (_old, _old_reason, queries) in zip(translations, labels)]
        return [
            {"id": f"D{idx}", "metric": metric, "decision_use": reason, "preferred_sources": ["official data", "filings", "annual reports", "industry association", "credible research"], "search_queries": queries}
            for idx, (metric, reason, queries) in enumerate(labels, start=1)
        ]

    @staticmethod
    def _dedupe_texts(values: List[Any]) -> List[str]:
        out = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            key = re.sub(r"\W+", "", text.lower())
            if text and key and key not in seen:
                seen.add(key)
                out.append(text)
        return out

    @staticmethod
    def _norm_key(value: str) -> str:
        return re.sub(r"\W+", "", str(value or "").lower())[:160]

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
