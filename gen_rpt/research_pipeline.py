from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .deepseek_client import DeepSeekClient
from .graphics import create_chart, create_insight_card, ensure_dir
from .pdf_renderer import render_pdf_from_html
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
        report = self._synthesize_report(topic, plan, sources)
        asset_map = self._materialize_assets(report, assets_dir)

        html_path = render_report_html(
            report=report,
            assets=asset_map,
            output_file=output_dir / "report.html",
            topic=topic,
            language=self.language,
        )
        markdown_path = render_report_markdown(
            report=report,
            assets=asset_map,
            output_file=output_dir / "report.md",
            topic=topic,
            language=self.language,
        )
        pdf_path = render_pdf_from_html(html_path, output_dir / "report.pdf")

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
            "language": self.language,
            "target_length": self.target_length,
            "html_path": str(html_path),
            "markdown_path": str(markdown_path),
            "pdf_path": str(pdf_path),
        }

    def _lang_instruction(self) -> str:
        return "Use English for the whole report." if self.language == "en" else "全程使用中文输出。"

    def _length_instruction(self) -> str:
        if self.language == "en":
            return f"Target around {self.target_length} words for the full report body and summary combined."
        return f"全文正文与摘要合计目标约 {self.target_length} 个中文字符。"

    def _plan_research(self, topic: str) -> Dict:
        system = (
            "You are a world-class deep research planner. "
            "Design a plan that follows the common Deep Research workflow: plan -> search -> read -> synthesize -> render. "
            "Return JSON only."
        )
        if self.language == "en":
            user = f"""
Create a research plan for the following topic and return JSON only.

Topic: {topic}

Required JSON fields:
- objective
- audience
- search_queries: 4-6 public web search queries in English when possible
- outline: 4-6 section titles
- chart_ideas: 2-3 chart opportunities
- insight_card_ideas: 2-3 executive insight card ideas
- risks: data or evidence risks

Requirements:
- Use English
- Keep search queries search-engine friendly
- Keep chart and insight-card concepts concise enough for clean visual rendering
- Do not output markdown
"""
        else:
            user = f"""
为下面这个选题生成一份研究计划，输出 JSON：

选题：{topic}

JSON 字段要求：
- objective: 研究目标
- audience: 目标读者
- search_queries: 4-6 个适合公开网络检索的查询语句
- outline: 4-6 个章节标题
- chart_ideas: 2-3 个适合图表化的角度
- insight_card_ideas: 2-3 个适合做高管洞察图卡的角度
- risks: 研究中可能出现的数据风险或口径风险

要求：
- 默认使用中文
- 查询语句尽量适合搜索引擎
- 图表主题和图卡主题尽量简洁，便于后续可视化呈现
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

        source_text = "\n\n".join(source_blocks)
        if not source_text:
            source_text = (
                "Insufficient web evidence was fetched. Build a clear analysis framework and explicitly mark where more evidence is needed."
                if self.language == "en"
                else "暂无抓取到足够网页资料，请基于选题输出可执行的分析框架，并明确指出需要后续补充外部证据。"
            )

        system = (
            "You are an elite research writer and strategy consultant. "
            "Use only the provided source material as factual grounding. "
            "If numbers are weak or partial, mark them as indicative in captions or notes. "
            "Return strict JSON only."
        )
        if self.language == "en":
            user = f"""
Based on the topic, research plan, and public web materials below, generate a rich research report data structure and return JSON only.

Topic:
{topic}

Language rule:
{self._lang_instruction()}

Length rule:
{self._length_instruction()}

Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Sources:
{source_text}

Required JSON fields:
- report_title
- report_subtitle
- executive_summary: 3-5 bullets
- sections: array, each item contains
  - id
  - title
  - lead
  - paragraphs: 3-5 paragraphs
  - key_takeaways: 3 bullets
  - visual_hint: image id such as card-1 or chart-1
- insight_cards: array with 2-3 items, each contains
  - id
  - title
  - subtitle
  - bullets: 3-4 items
  - highlight_number
  - highlight_label
  - exhibit_label
- charts: array with 1-3 items, each contains
  - id
  - title
  - subtitle
  - type: bar / line / pie
  - categories
  - series: [{{"name": "", "values": []}}]
  - x_label
  - y_label
  - caption
  - source_note
- references: array, each contains
  - title
  - url
  - note

Hard requirements:
1. Use English throughout.
2. Make it feel like a Deep Research deliverable: define the problem, map the current state, analyze trends, then recommend actions.
3. Keep the writing specific and professional.
4. When a section is text-heavy, point visual_hint to a relevant card or chart.
5. If chart data is approximate or synthesized, clearly say so in caption or source_note.
6. references may only use real URLs that appear in the source materials.
7. Keep visual text short enough to avoid overlapping when rendered:
   - insight card title <= 12 English words
   - insight card subtitle <= 24 English words
   - each insight card bullet <= 16 English words
   - chart title <= 12 English words
   - chart subtitle <= 18 English words
   - category labels should be compact
8. Do not include meta labels such as 'McKinsey-style', 'consulting-style', 'sample card', or production notes in any final report text.
9. Do not output markdown. Output JSON only.
"""
        else:
            user = f"""
请基于以下选题、研究计划和公开资料，生成一份图文并茂研究报告的数据结构，输出 JSON。

选题：{topic}

语言要求：
{self._lang_instruction()}

篇幅要求：
{self._length_instruction()}

研究计划：
{json.dumps(plan, ensure_ascii=False, indent=2)}

资料：
{source_text}

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
  - exhibit_label
- charts: 数组，1-3 项，每项包含
  - id
  - title
  - subtitle
  - type: bar / line / pie
  - categories
  - series: [{{"name": "", "values": []}}]
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
7. 为避免图像排版拥挤，请尽量压缩可视化文本长度：
   - 图卡标题尽量不超过 18 个中文字符
   - 图卡副标题尽量不超过 32 个中文字符
   - 图卡 bullet 每条尽量不超过 24 个中文字符
   - 图表标题尽量不超过 18 个中文字符
   - 图表副标题尽量不超过 28 个中文字符
   - 类目标签尽量短
8. 不要在最终内容中出现类似“McKinsey-style”“consulting-style”“样例图卡”“制作说明”之类的元描述。
9. 不要输出 markdown，只输出 JSON。
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
