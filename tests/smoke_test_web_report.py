from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.brand_assets import copy_or_generate_brand_assets
from gen_rpt.research_quality import build_research_fact_pack
from gen_rpt.web_evidence import build_evidence_exhibits, build_evidence_ledger, build_storyline_plan
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_report_renderer import normalize_web_report, render_web_report_html, render_web_report_markdown


def main() -> None:
    out = ROOT / ".tmp_web_smoke"
    if out.exists():
        shutil.rmtree(out)
    assets_dir = out / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets = copy_or_generate_brand_assets(assets_dir)

    plan = {
        "objective": "Assess how AI can offset the process-industry talent cliff.",
        "decision_question": "Where should process-industry leaders deploy AI first to reduce workforce-driven output risk?",
        "search_queries": [
            "process industries talent cliff AI productivity 2026",
            "AI powered marketing revenue growth BCG 2024",
            "responsible AI industrial operations governance 2026",
        ],
        "outline": [
            "The talent cliff is an operating risk before it is an HR issue",
            "AI value depends on frontline workflow redesign",
            "Evidence gates should control scale-up",
        ],
    }
    sample_sources = [
        SourceDocument(
            title="BCG sample talent cliff article",
            url="https://www.bcg.com/publications/2026/ai-the-answer-to-process-industries-talent-cliff",
            query="process industries talent cliff AI productivity 2026",
            snippet="The article frames process industries as facing a talent cliff in 2026.",
            content=(
                "The sample article cites a 15% decline in workforce hours against a 10% decline in output across 2002-2024. "
                "It frames process industries as including metals and mining, chemicals, agriculture, forestry, paper and packaging."
            ),
            source_type="html",
            domain="bcg.com",
        ),
        SourceDocument(
            title="BCG sample AI marketing article",
            url="https://www.bcg.com/publications/2024/blueprint-for-ai-powered-marketing",
            query="AI powered marketing revenue growth BCG 2024",
            snippet="The article references AI leaders and growth outcomes.",
            content=(
                "The sample AI marketing article references 60% greater revenue growth for AI leaders. "
                "It uses the next five years as the transformation horizon and describes four stages from foundation to transformation."
            ),
            source_type="html",
            domain="bcg.com",
        ),
        SourceDocument(
            title="BCG AI capabilities page",
            url="https://www.bcg.com/capabilities/artificial-intelligence",
            query="AI capabilities operating model",
            snippet="AI programs need operating-model change and adoption routines.",
            content=(
                "Industrial AI adoption depends on data access, process redesign, human trust and responsible governance. "
                "A 90-day pilot can test adoption, but lifecycle ROI requires a longer validation window."
            ),
            source_type="html",
            domain="bcg.com",
        ),
        SourceDocument(
            title="BCG responsible AI page",
            url="https://www.bcg.com/capabilities/artificial-intelligence/responsible-ai",
            query="responsible AI industrial operations governance 2026",
            snippet="Responsible AI requires governance, human oversight and risk controls.",
            content=(
                "Responsible AI programs commonly separate advisory AI from autonomous decisions. "
                "A 30/90/180-day cadence can assign owners, validation gates and measurable site-level outcomes in 2026."
            ),
            source_type="html",
            domain="bcg.com",
        ),
    ]
    fact_pack = build_research_fact_pack("AI and process-industry talent", plan, sample_sources)
    evidence_ledger = build_evidence_ledger("AI and process-industry talent", sample_sources, fact_pack)
    storyline_plan = build_storyline_plan("AI and process-industry talent", plan, fact_pack, evidence_ledger, language="en")
    evidence_exhibits = build_evidence_exhibits("AI and process-industry talent", evidence_ledger, fact_pack, language="en")
    assert len(evidence_ledger) >= 3
    assert 3 <= len(evidence_exhibits) <= 5
    assert all(exhibit.get("data_basis") for exhibit in evidence_exhibits)

    payload = {
        "title": "AI Can Rebuild the Industrial Talent Model Only If Leaders Treat It as Operating Redesign",
        "dek": "Process industries face a workforce cliff, but AI creates value only when it changes frontline work, skills, incentives and management routines together.",
        "category": "Industrial goods",
        "authors": ["BlueOcean Research"],
        "source_count": fact_pack.source_count,
        "intro": [
            "The core question is not whether AI can automate tasks. It is whether management can redesign work fast enough to protect output, retain institutional knowledge and make frontline roles more attractive."
            " By 2026, that matters because labor pressure is no longer a slow-burn HR issue; it is already shaping throughput, safety and training capacity at site level."
        ],
        "key_takeaways": [
            "The labor gap is a productivity and knowledge-transfer problem, not just a hiring problem.",
            "AI use cases should start where retirements, vacancies and process variability create measurable output risk.",
            "The winning operating model pairs AI copilots with frontline workflow redesign, not standalone tools.",
        ],
        "sections": [
            {
                "id": "section-1",
                "title": "The talent cliff is becoming an output risk because process knowledge is leaving faster than hiring can replace it",
                "lead": "Aging workforces, fewer qualified applicants and early-tenure attrition combine to make the labor shortage a direct production constraint.",
                "paragraphs": [
                    "Process industries depend on tacit know-how: operators recognize weak signals, maintenance teams diagnose equipment behavior and supervisors translate plant conditions into daily decisions.",
                    "When experienced workers retire, companies lose more than headcount. They lose pattern recognition, escalation judgment and local operating memory that rarely exists in manuals.",
                    "A hiring-only response is too slow because the skills pipeline does not refill at the same pace as retirement and attrition.",
                    "AI is relevant because it can capture, structure and distribute operating knowledge at the moment of work.",
                    "The practical test is whether AI improves throughput, safety, uptime or training time enough to offset workforce pressure.",
                    "The sample BCG talent article cites workforce-hour declines of 15% against output declines of 10%, which is the kind of productivity bridge management should validate at site level before funding a full rollout.",
                    "For a 2026 leadership team, the implication is to connect labor metrics with operating metrics in the same dashboard rather than treating workforce planning as an HR-only issue.",
                ],
                "evidence": [
                    "Public labor data and industry surveys point to aging workforces and weaker conversion of job postings into successful hires.",
                    "BCG's sample article frames process industries as including metals and mining, chemicals, agriculture, forestry, paper and packaging.",
                    "The sample article points to 2002-2024 productivity growth as a relevant benchmark period for the industrial talent question.",
                ],
                "so_what": "Leadership should prioritize AI where knowledge loss is already visible in output, uptime, safety or training metrics.",
            },
            {
                "id": "section-2",
                "title": "The highest-value AI use cases sit closest to frontline decisions, not in generic enterprise productivity tools",
                "lead": "Use cases should be selected by their ability to absorb scarce expertise and reduce variation in daily operations.",
                "paragraphs": [
                    "Many companies start with back-office copilots because they are easy to deploy. That misses the more material constraint in process industries.",
                    "Frontline AI can help operators query procedures, compare abnormal conditions with historical cases and escalate exceptions with richer context.",
                    "Maintenance copilots can reduce diagnostic time by combining sensor data, work orders and equipment history.",
                    "Training copilots can shorten the path from classroom instruction to competent field performance.",
                    "The value case should be measured in avoided downtime, faster onboarding, fewer quality deviations and safer shift execution.",
                    "A 90-day pilot is long enough to test adoption, but too short to prove lifecycle ROI; management should treat it as a decision gate for deeper investment, not as a transformation claim.",
                    "The AI marketing sample shows why use-case choice matters: AI leaders win by changing testing speed, budget allocation and creative workflows, not by spreading generic tools evenly across the function.",
                ],
                "evidence": [
                    "AI-mature industrial companies are often described as outperforming less mature peers on growth and returns, but company-specific validation is still required.",
                    "Reliable value cases require baseline data on downtime, turnover, training time and safety incidents.",
                    "The marketing sample references 60% greater revenue growth for AI leaders; an industrial version would need similarly explicit value metrics before becoming board-ready.",
                ],
                "so_what": "The first AI portfolio should be built from operational pain points, then translated into a data and workflow roadmap.",
            },
            {
                "id": "section-3",
                "title": "A durable AI advantage requires workflow redesign before technology scale-up",
                "lead": "Tools create adoption friction unless roles, routines, governance and incentives change with them.",
                "paragraphs": [
                    "The trap is to deploy AI as an overlay on unchanged work. That produces pilots, demos and dashboards but little sustained behavior change.",
                    "Operators need clear escalation rules when AI recommendations conflict with experience or safety protocols.",
                    "Supervisors need routines for reviewing AI-assisted decisions and converting recurring exceptions into process improvements.",
                    "HR and operations need a joint skills model so AI becomes part of career progression rather than a perceived threat.",
                    "Governance needs to distinguish advisory AI from autonomous control, especially in safety-critical settings.",
                    "A practical 2026 roadmap should set separate gates for data access, frontline trust, cyber review, safety validation and labor adoption because each gate can stop scale-up independently.",
                    "The operating model should also define what happens when AI is wrong: who overrides it, who reviews the event and how the lesson becomes part of the next shift's standard work.",
                ],
                "evidence": [
                    "Industrial AI adoption depends on data access, process redesign, human trust and responsible governance.",
                    "Evidence gaps remain around the exact ROI of each workflow change and should be validated through pilots.",
                    "BCG's AI marketing sample uses four stages from foundation to transformation; the industrial equivalent needs additional safety and reliability gates.",
                ],
                "so_what": "Scale decisions should follow workflow adoption evidence, not just model accuracy or vendor readiness.",
            },
            {
                "id": "section-4",
                "title": "The management agenda should move from pilots to a staged operating-model program",
                "lead": "The right sequencing is to prove value in constrained workflows, codify the operating model and then scale across sites.",
                "paragraphs": [
                    "A staged program reduces risk because each phase has a different proof point.",
                    "The first phase should identify where workforce constraints are already damaging operational performance.",
                    "The second phase should build a small set of AI-enabled workflows with explicit human-in-the-loop controls.",
                    "The third phase should standardize training, data ownership, cybersecurity and performance management.",
                    "The fourth phase should scale across plants only after adoption and value metrics are visible.",
                    "The first 30 days should produce a quantified exposure map; the next 60 days should test two or three workflows; the following 90 days should determine whether scale-up is justified.",
                    "That sequence changes the executive conversation from 'Should we deploy AI?' to 'Which operating risks are now sufficiently evidenced to fund at the next gate?'",
                    "BCG's marketing sample uses a four-stage path from foundation to transformation and cites the next five years as the relevant horizon; the industrial version should be equally explicit about a 2026 30/90/180-day cadence before any broad rollout.",
                    "The right board question in 2026 is not whether the company owns AI tools, but whether the company can show a 2026 operating calendar with named owners, proof gates and measurable site-level outcomes.",
                ],
                "evidence": [
                    "A phased roadmap is consistent with how BCG's AI marketing sample moves from foundation to scaling, leading capabilities and transformation.",
                    "Industrial settings require additional gates for safety, reliability and labor relations.",
                    "A four-stage program creates visible decision points at roughly 30, 90 and 180 days before broader rollout.",
                    "The sample BCG marketing article explicitly frames its transformation horizon as the next five years, which is a useful planning anchor for the industrial case.",
                ],
                "so_what": "Management should fund an operating-model program, not a technology experiment.",
            },
            {
                "id": "section-5",
                "title": "The leadership dashboard should connect labor, output and adoption metrics in one operating view",
                "lead": "The board-level dashboard should show whether AI is actually absorbing the labor shock rather than merely creating a new software layer.",
                "paragraphs": [
                    "A useful dashboard should sit above the individual pilots and show whether the business is reducing the gap between labor availability and operating demand.",
                    "The first view should be site exposure: where do retirements, vacancies and turnover create the highest production risk in 2026?",
                    "The second view should be performance: are throughput, uptime, quality and safety improving after AI is introduced?",
                    "The third view should be adoption: are frontline teams actually using the tools, or are supervisors bypassing them and reverting to manual workarounds?",
                    "The fourth view should be capability: how many people have moved through the new training path, and how quickly can the company ramp new hires by quarter?",
                    "The fifth view should be economics: what is the change in avoided downtime, rework, training hours and incident risk once the workflow redesign is in place?",
                    "If the team cannot show these numbers by the end of the first quarter of 2026, the program is not yet a leadership solution; it is still an experiment.",
                ],
                "evidence": [
                    "The sample article's 15% workforce-hour decline against 10% output decline is the right kind of metric bridge for a leadership dashboard.",
                    "The 2002-2024 productivity benchmark is a reminder that the relevant comparison period is long enough to show structural drift, not just a one-quarter fluctuation.",
                    "Quarterly operating views are common in BCG-style thought leadership because they make the implied management cadence visible to the reader.",
                ],
                "so_what": "The dashboard should prove that AI is changing the operating trajectory, not just the software stack.",
            },
        ],
        "exhibits": evidence_exhibits,
        "action_steps": [
            {"horizon": "0-30 days", "action": "Quantify workforce exposure by site and workflow", "success_metric": "Baseline dashboard covers vacancies, retirement risk, downtime, training time and safety."},
            {"horizon": "30-90 days", "action": "Launch two AI-enabled frontline workflow pilots", "success_metric": "Pilots have adoption, value and safety gates."},
            {"horizon": "90-180 days", "action": "Codify the AI operating model for scale", "success_metric": "Roles, data ownership, governance and training are approved for rollout."},
        ],
        "methodology": "Smoke-test payload based on public-source fixtures, fact-pack extraction and an evidence ledger. Exhibit values are traceable to the retained fixture sources.",
        "evidence_quality": f"The smoke test retained {len(evidence_ledger)} evidence-ledger points and {fact_pack.source_count} public sources.",
        "references": [
            {"title": "BCG sample talent cliff article", "url": "https://www.bcg.com/publications/2026/ai-the-answer-to-process-industries-talent-cliff"},
            {"title": "BCG sample AI marketing article", "url": "https://www.bcg.com/publications/2024/blueprint-for-ai-powered-marketing"},
            {"title": "BCG AI capabilities page", "url": "https://www.bcg.com/capabilities/artificial-intelligence"},
            {"title": "BCG responsible AI page", "url": "https://www.bcg.com/capabilities/artificial-intelligence/responsible-ai"},
        ],
    }

    normalized = normalize_web_report(payload, topic="AI and process-industry talent", language="en")
    assert normalized["title"].startswith("AI Can Rebuild")
    assert len(normalized["key_takeaways"]) == 3
    assert len(normalized["sections"]) == 5
    assert 3 <= len(normalized["exhibits"]) <= 5
    assert normalized["source_count"] == fact_pack.source_count
    drifted_payload = dict(payload)
    drifted_payload.pop("key_takeaways", None)
    drifted_payload["keyTakeaways"] = {
        "items": [
            {
                "claim": "Field naming drift should not remove the executive takeaway module.",
                "implication": "The persisted payload must normalize camelCase model output before audit.",
            },
            {
                "claim": "Dict-shaped takeaways should retain both the claim and management implication.",
                "implication": "This keeps the article useful for a CEO or board reader.",
            },
            {
                "claim": "Extra generated takeaways should be trimmed to the required three.",
                "implication": "The audit contract remains strict instead of depending on renderer slicing.",
            },
            {
                "claim": "This fourth item should be trimmed.",
                "implication": "The payload must still contain exactly three takeaways.",
            },
        ]
    }
    drifted_normalized = normalize_web_report(drifted_payload, topic="AI and process-industry talent", language="en")
    assert len(drifted_normalized["key_takeaways"]) == 3
    assert "camelCase" in drifted_normalized["key_takeaways"][0]

    html_path = render_web_report_html(payload, assets, out / "index.html", "AI and process-industry talent", "en")
    md_path = render_web_report_markdown(payload, out / "report.md", "AI and process-industry talent", "en")
    assert html_path.exists()
    assert md_path.exists()
    html_text = html_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    assert "Key Takeaways" in html_text
    assert "article-shell" in html_text
    assert "Data basis" in html_text
    assert "opportunity matrix" in html_text.lower()
    assert "How leaders should move next" in html_text
    assert "BCG sample talent cliff article" in html_text
    assert "# AI Can Rebuild" in md_text
    (out / "web_report_payload.json").write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "evidence_ledger.json").write_text(json.dumps(evidence_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "storyline_plan.json").write_text(json.dumps(storyline_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "sources.json").write_text(json.dumps([source.__dict__ for source in sample_sources], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "normalized.json").write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HTML smoke report: {html_path}")


if __name__ == "__main__":
    main()
