from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .deepseek_client import DeepSeekClient
from .graphics import create_chart, create_insight_card, ensure_dir
from .report_renderer import render_report_html
from .web_fetch import SourceDocument, collect_sources


class ResearchPipeline:
    def __init__(self, client: DeepSeekClient, language: str = "zh") -> None:
        self.client = client
        self.language = language

    def build_report(self, topic: str, output_dir: Path) -> Dict:
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)

        plan = self._plan_research(topic)
        queries = plan.get("search_queries", [])[:4]
        sources = collect_sources(queries, per_query=3, max_sources=8)
        report = self._synthesize_report(topic, plan, sources)
        asset_map = self._materialize_assets(report, assets_dir)

        render_report_html(report=report, assets=asset_map, output_file=output_dir / "report.html", topic=topic)
        (output_dir / "report_payload.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "research_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "sources.json").write_text(
            json.dumps([source.__dict__ for source in sources], ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "plan": plan,
            "sources": [source.__dict__ for source in sources],
            "report": report,
            "asset_map": asset_map,
            "output_dir": str(output_dir),
        }

    def _plan_research(self, topic: str) -> Dict:
        system = (
            "You are a world-class deep research planner. "
            "Design a plan that follows the common Deep Research workflow: plan -> search -> read -> synthesize -> render. "
            "Return JSON only."
        )
        user = f"""
为下面这个选题生成一份研究计划，输出 JSON：

选题：{topic}

JSON 字段要求：
- objective: 研究目标
- audience: 目标读者
- search_queries: 4-6 个适合公开网络检索的查询语句
- outline: 4-6 个章节标题
- chart_ideas: 2-3 个适合图表化的角度
- insight_card_ideas: 2-3 个适合做咨询风格图卡的角度
- risks: 研究中可能出现的数据风险或口径风险

要求：
- 默认使用中文
- 查询语句尽量适合搜索引擎
- 不要输出 markdown，只输出 JSON
"""
        return self.client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    def _synthesize_report(self, topic: str, plan: Dict, sources: List[SourceDocument]) -> Dict:
        source_blocks = []
        for idx, src in enumerate(sources, start=1):
            excerpt = src.content[:2500]
            source_blocks.append(
                f"[Source {idx}]\nTitle: {src.title}\nURL: {src.url}\nSearch Query: {src.query}\nSnippet: {src.snippet}\nExcerpt:\n{excerpt}"
            )

        system = (
            "You are an elite research writer and strategy consultant. "
            "Use only the provided source material as factual grounding. "
            "If numbers are weak or partial, mark them as indicative in captions or notes. "
            "Return strict JSON only."
        )
        user = f"""
请基于以下选题、研究计划和公开资料，生成一份图文并茂研究报告的数据结构，输出 JSON。

选题：{topic}

研究计划：
{json.dumps(plan, ensure_ascii=False, indent=2)}

资料：
{'\n\n'.join(source_blocks) if source_blocks else '暂无抓取到足够网页资料，请基于选题输出可执行的分析框架，并明确指出需要后续补充外部证据。'}

JSON 字段要求：
- report_title: 报告标题
- report_subtitle: 报告副标题
- executive_summary: 3-5 条执行摘要
- sections: 数组，每项包含
  - id
  - title
  - lead
  - paragraphs: 3-5 段正文
  - key_takeaways: 3 条要点
  - visual_hint: 使用的图像 id，例如 card-1 或 chart-1
- insight_cards: 数组，2-3 项，每项包含
  - id
  - title
  - subtitle
  - bullets: 3-4 条
  - highlight_number
  - highlight_label
- charts: 数组，1-3 项，每项包含
  - id
  - title
  - type: bar / line / pie
  - categories
  - series: [{"name": "", "values": []}]
  - x_label
  - y_label
  - caption
  - source_note
- references: 数组，每项包含
  - title
  - url
  - note

硬性要求：
1. 默认使用中文。
2. 报告结构要有“像 Deep Research”的感觉：先定义问题，再梳理现状，再分析趋势，再给建议。
3. 正文要专业，但避免空话。
4. 如果某个章节文字较多，请把 visual_hint 指向合适的图卡或图表。
5. 图表数据若为概括性整理，请在 caption 或 source_note 里明确写“示意性整理”或等价说明。
6. references 只允许使用资料区里真实出现过的 URL。
7. 不要输出 markdown，只输出 JSON。
"""
        return self.client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.25,
        )

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
