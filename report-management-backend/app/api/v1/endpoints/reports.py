from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from app.api.deps import get_db, PageParams, FilterParams, get_current_user_placeholder
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()


# Global mock state to allow frontend updates to persist across API calls
MOCK_REPORTS = {
    "doc-1111-approved": {
        "id": "doc-1111-approved", "title": "Nuclear Fusion Commercialization", "version": "1.2",
        "status": "Approved", "humanStatus": "Final Review Complete", "aiScore": 92, "aiGrade": "Gold",
        "commentCount": 0, "lastUpdated": "2026-06-30T10:00:00Z", "publishReady": True, 
        "aiReview": {
            "scores": {
                "overall_score": 92,
                "grade": "Gold",
                "components": {
                    "research_quality": 28,
                    "strategic_insight": 22,
                    "source_quality": 22,
                    "writing_quality": 10,
                    "design_quality": 5,
                    "executive_readiness": 5
                }
            },
            "recommendations": {
                "strengths": [
                    "Comprehensive analysis of fusion timelines and rigorous examination of cost projections.",
                    "Excellent triangulation of authoritative government sources (DOE, National Academies) with private sector data."
                ],
                "weaknesses": [
                    "Economic analysis lacks sensitivity scenarios for carbon pricing impacts on fusion LCOE.",
                    "Could be strengthened with a more explicit visual bridge for market sizing."
                ],
                "priority_improvements": [
                    {
                        "issue": "Missing Carbon Pricing Scenarios",
                        "impact": "Reduces clarity on exactly when fusion might cross the competitiveness threshold against renewables.",
                        "suggested_fix": "Add a sensitivity table showing fusion LCOE vs Solar/Wind at $50, $100, and $150/ton carbon prices.",
                        "priority_level": "High"
                    },
                    {
                        "issue": "Tritium Supply Chain Detail",
                        "impact": "Leaves a critical bottleneck under-explained.",
                        "suggested_fix": "Expand the paragraph on Tritium supply to include specific global inventory projections.",
                        "priority_level": "Medium"
                    }
                ],
                "executive_readiness": {
                    "board_members": True,
                    "ministers": True,
                    "ceos": True,
                    "sovereign_wealth_funds": False,
                    "senior_executives": True,
                    "justification": "The report is highly analytical and directly addresses strategic implications. It is ready for corporate and policy leadership, though SWF audiences might require a deeper dive into capital deployment vehicles."
                }
            },
            "claims_audit": {
                "claims": [
                    {
                        "claim": "Commercial grid-scale fusion electricity is unlikely before 2040",
                        "classification": "supported",
                        "evidence": "Supported by the National Academies' 2021 report and updated ITER timelines.",
                        "confidence": 0.95
                    },
                    {
                        "claim": "First-of-a-kind fusion plants will have an LCOE of $100-200/MWh",
                        "classification": "partially_supported",
                        "evidence": "Consistent with independent analyses, but some private models project lower. Acknowledged as highly uncertain.",
                        "confidence": 0.85
                    }
                ]
            }
        },
        "pdfPath": "reports/originals/doc-1111-approved.pdf",
        "coverImagePath": "reports/images/doc-1111-approved-cover.png",
                "reportContent": {
            "brand": "GateX", 
            "label": "Approved", 
            "date": "2026-06-27", 
            "sections": [
    {
        "heading": "Executive Summary",
        "body": "Nuclear fusion has long been the holy grail of clean energy\u2014abundant, safe, and virtually limitless. In the past two years, private investment has surged past $9 billion, the U.S. Department of Energy has released a Fusion Science & Technology Roadmap targeting commercial power by the mid-2030s, and Lawrence Livermore National Laboratory achieved the first controlled fusion ignition in history. These milestones have sparked a wave of optimism, with some declaring fusion is '10 years away.' But a rigorous examination of project timelines, cost projections, regulatory progress, and supply-chain constraints tells a more sobering story. This report assesses the commercial viability and strategic implications of nuclear fusion, drawing on authoritative government sources, industry data, and independent expert assessments. Our conclusion: fusion will not achieve commercial grid-scale electricity generation before 2040, and its levelized cost will remain uncompetitive with renewables and gas for at least another decade. The strategic imperative for organizations today is not to bet the farm on fusion, but to prepare for its eventual arrival while investing in technologies that can deliver emissions reductions now."
    },
    {
        "heading": "Fusion Is Not Coming Soon: The Technology Readiness Gap and Timeline Realities",
        "body": "**Despite the DOE's roadmap targeting commercial fusion by the mid-2030s, the world's most advanced projects\u2014ITER, SPARC, STEP\u2014all point to first commercial plants no earlier than 2040, and likely later.**\n\nThe DOE's Fusion Science & Technology Roadmap, released in 2025, sets an ambitious goal: 'deliver commercial fusion power to the grid by the mid-2030s.' However, this roadmap is explicitly contingent on 'the development of future public-private partnerships' and 'future funding subject to Congressional appropriations.' It is a vision, not a plan with committed resources.\n\nThe reality on the ground is more measured. ITER, the international tokamak under construction in France, has experienced repeated delays. Originally scheduled for first plasma in 2020, the project now targets first plasma in the 2030s, with full deuterium-tritium operations not expected until at least 2035. The National Academies' 2021 report 'Bringing Fusion to the U.S. Grid' noted that 'a schedule to bring a pilot plant into operation between 2035 and 2040 is aggressive relative to recent construction of large fusion facilities.'\n\nPrivate companies are more optimistic but still project timelines beyond 2030. Commonwealth Fusion Systems (CFS) aims for its SPARC device to demonstrate net energy in the late 2020s, with a first commercial plant (ARC) targeted for the early 2030s. However, CFS has not yet completed SPARC construction, and scaling from a demonstration to a commercial plant typically takes a decade or more. The UK's STEP program aims for a prototype plant by 2040. China's CFETR program targets operation in the 2030s but has not yet broken ground.\n\nThe DOE's Milestone-Based Fusion Development Program, inspired by NASA's public-private model, provides a useful reality check. Eight awardees are working on pre-conceptual designs and technology roadmaps, with initial milestones due in late 2025. The program has authorized $415 million through FY2027, but only $46 million has been obligated for the first 18 months. Private companies have raised over $350 million in new funding since May 2023\u2014a 7.6x leverage on federal dollars\u2014but this is a fraction of the estimated $50 billion needed for commercial viability.\n\nCounter-evidence: Some proponents argue that the rapid progress in private fusion\u2014over $9 billion in total private investment, with companies like Helion and TAE Technologies targeting commercial plants by 2030\u2014could accelerate timelines. However, no private company has yet demonstrated net energy gain in a reactor-relevant configuration. The 2022 LLNL ignition achievement was a single-shot inertial confinement experiment, not a sustained power plant. The gap between scientific breakeven and commercial power plant is vast and historically underestimated.\n\nThe bottom line: even the most optimistic independent assessments place first commercial fusion electricity in the 2040s. The DOE's mid-2030s target is a stretch goal, not a baseline. For strategic planning, organizations should assume fusion will not be a material contributor to electricity generation before 2040.\n\n\n**Key Evidence:**\n\n- The DOE Fusion Science & Technology Roadmap targets commercial fusion by the mid-2030s but is contingent on future public-private partnerships and appropriations (Source 13)\n\n- ITER, the world's largest fusion experiment, is now expected to achieve first plasma in the 2030s, with full operations after 2035 (Source 5)\n\n- The National Academies' 2021 report states that a U.S. fusion pilot plant between 2035 and 2040 is 'aggressive' compared to historical large-facility construction (Source 7)\n\n- The DOE Milestone Program has obligated only $46 million for the first 18 months, while private awardees have raised over $350 million in new funding since May 2023 (Source 3)\n\n- LLNL's 2022 fusion ignition produced 3.15 MJ from 2.05 MJ input, but this was a single-shot inertial confinement experiment, not a sustained power plant (Source 11)\n\n\n*For energy investors and corporate strategists, the implication is clear: fusion will not disrupt electricity markets in the next 10-15 years. Near-term clean energy investments should focus on solar, wind, battery storage, and advanced fission, which are commercially viable today. Fusion should be monitored as a long-term option, not a near-term bet.*"
    },
    {
        "heading": "The Economics Don't Work Yet: Fusion LCOE Comparisons and the Cost Challenge",
        "body": "**First-of-a-kind fusion plants are projected to have an LCOE of $100-200/MWh, well above the $20-60/MWh expected for solar and wind by 2035, making fusion uncompetitive without subsidies or high carbon prices.**\n\nThe levelized cost of electricity (LCOE) from fusion is highly uncertain, but all credible estimates point to a significant premium over renewables and gas for first-of-a-kind plants. Commonwealth Fusion Systems has publicly targeted an LCOE of $50-70/MWh for its ARC plant, but this is a target, not a demonstrated cost. Independent analyses, including those from the IEA and academic studies, project first-of-a-kind fusion LCOE in the range of $100-200/MWh, driven by high capital costs, complex supply chains, and the need for tritium breeding systems.\n\nBy contrast, Lazard's 2024 LCOE analysis shows solar PV at $20-40/MWh, onshore wind at $30-60/MWh, and combined-cycle gas at $40-80/MWh. Even with carbon pricing of $50-100/ton, fusion would struggle to compete with renewables in most markets. The IEA's World Energy Outlook projects that solar and wind will remain the cheapest sources of new electricity through 2050, with costs continuing to decline.\n\nFusion's cost disadvantage is compounded by its capital intensity. A single 1 GW fusion plant is estimated to cost $5-10 billion, compared to $1-2 billion for a solar farm of equivalent capacity. The high upfront cost creates significant financing risk, especially for first-of-a-kind plants that lack operating history. The DOE's Milestone Program requires private companies to provide more than 50% of the cost to meet milestones, underscoring the capital burden on developers.\n\nCounter-evidence: Fusion advocates argue that LCOE will fall rapidly with learning, as it did for solar and wind. However, fusion plants are complex, capital-intensive facilities with long construction times, unlike modular solar panels. The learning rate for nuclear fission has been flat or negative in many markets, and fusion is likely to follow a similar trajectory. Additionally, fusion's high capacity factor (90% assumed) could provide value as baseload power, but this advantage is eroded by the falling cost of renewables-plus-storage.\n\nThe value pool for fusion extends beyond electricity sales. Carbon credits, capacity payments, and industrial heat applications could improve the economics. The DOE's roadmap includes a focus on closing the fusion cycle, including an Integrated Blanket and Fuel Cycle Test Facility, which could enable hydrogen production and other non-electric applications. However, these markets are smaller and less certain than bulk electricity generation.\n\nThe bottom line: fusion will not be cost-competitive with renewables and gas for at least a decade after first commercial plants come online. Organizations should not expect fusion to provide cheap electricity in the 2030s. If fusion is to play a role, it will likely require policy support, such as carbon pricing or production tax credits, to bridge the cost gap.\n\n\n**Key Evidence:**\n\n- The DOE Milestone Program requires private companies to provide more than 50% of the cost to meet milestones, indicating high capital intensity (Source 3)\n\n- The DOE FY2027 budget request includes $755.3 million for Fusion Energy Sciences, a decrease of $50.4 million from FY2026, with reduced funding for core research (Source 14)\n\n- The DOE's roadmap includes closing the fusion cycle with an Integrated Blanket and Fuel Cycle Test Facility, enabling non-electric applications (Source 14)\n\n\n*For corporate strategists, the cost outlook means fusion is unlikely to be a competitive source of electricity in the next 15 years. Investments in fusion should be framed as long-term R&D or strategic options, not as near-term cost-effective energy solutions. Companies should focus on reducing their own carbon footprint using commercially available renewables and efficiency measures.*"
    },
    {
        "heading": "Policy and Funding Are Accelerating, but Not Enough: Government and Private Investment Trends",
        "body": "**Government funding for fusion R&D has increased, but the U.S. FY2027 budget request shows a decline, and total public funding remains below $1 billion per year in major economies\u2014far from the tens of billions needed to commercialize fusion.**\n\nGovernment support for fusion has grown significantly in recent years. The U.S. DOE's Fusion Energy Sciences (FES) program budget reached $755.3 million in the FY2027 request, though this is a decrease of $50.4 million from FY2026 enacted levels. The DOE's Fusion Science & Technology Roadmap and the Milestone-Based Fusion Development Program signal strong policy intent, but funding remains modest relative to the scale of the challenge.\n\nThe Fusion Industry Association's 2024 report found that total public funding for fusion increased by 57% in the last 12 months to $426 million globally. However, this is still a fraction of the $50 billion or more that private companies are expected to need before achieving commercial viability. The U.S. CHIPS and Science Act of 2022 authorized $415 million for the Milestone Program through FY2027, but only $46 million has been obligated so far.\n\nPrivate investment has been more robust. The FIA reports that total private fusion investment has surpassed $9 billion, with over $2.5 billion raised in the last 12 months alone. Notable deals include $100 million for Xcimer, $90 million for SHINE, and $65 million for Helion. The U.S. is the global leader with 25 private fusion companies, followed by the UK, Germany, Japan, and China.\n\nCounter-evidence: The surge in private investment could be seen as a vote of confidence that fusion is nearing commercialization. However, the capital intensity of fusion means that $9 billion is still a down payment. The FIA's 2025 report notes 'maturing investor confidence,' but also that the industry is 'just shy of $10bn' in total funding. For context, the global solar industry attracted over $150 billion in investment in 2023 alone. Fusion's funding, while growing, is still niche.\n\nGovernment funding is also uneven. The U.S. FY2027 budget request reduces core research funding to offset increases for high-priority activities and facility operations. The DOE's ability to support the roadmap's milestones is 'contingent on the development of future public-private partnerships' and future appropriations. In the EU, Horizon Europe provides fusion funding, but the overall budget is constrained. China's fusion budget is less transparent but is believed to be growing, with the CFETR program as a centerpiece.\n\nThe bottom line: while policy support and private investment are increasing, they are not yet at levels that would dramatically accelerate fusion timelines. The gap between current funding and the estimated $50 billion needed for commercial viability is vast. Organizations should not assume that government funding will bridge this gap quickly.\n\n\n**Key Evidence:**\n\n- The DOE FY2027 FES budget request is $755.3 million, a decrease of $50.4 million from FY2026 enacted (Source 14)\n\n- The Fusion Industry Association reports total public fusion funding increased 57% to $426 million in 2024 (Source 9)\n\n- Total private fusion investment has surpassed $9 billion, with over $2.5 billion raised in the last 12 months (Source 10)\n\n- The Milestone Program is authorized for $415 million through FY2027, but only $46 million has been obligated so far (Source 3)\n\n- Private fusion companies have raised over $350 million in new funding since May 2023, compared to $46 million in federal commitments (Source 3)\n\n\n*For investors and strategists, the funding trends suggest that fusion is still a high-risk, long-duration bet. The private sector is leading, but the capital required is enormous. Organizations should monitor funding levels as a leading indicator of industry health, but should not expect a near-term policy-driven acceleration. Strategic partnerships with fusion companies should be structured as options with long.*"
    },
    {
        "heading": "The Market Will Be Niche for Decades: Adoption Scenarios and Addressable Markets",
        "body": "**Under all credible scenarios, fusion will capture less than 1% of global electricity generation by 2035 and at most 5% by 2050, limiting its near-term impact on energy markets.**\n\nEven under optimistic assumptions, fusion adoption will be slow. The IEA's World Energy Outlook and BloombergNEF's New Energy Outlook both project that fusion will provide less than 1% of global electricity by 2035, and only 2-5% by 2050 under the most aggressive scenarios. This is due to long construction times, regulatory hurdles, and the need to build a new supply chain from scratch.\n\nThe National Academies' pilot plant strategy envisions a first U.S. pilot plant operating between 2035 and 2040, with a second phase by 2040-2045. This implies that commercial-scale plants would not begin to proliferate until the 2040s at the earliest. Given that a typical nuclear plant takes 5-10 years to build, even a rapid build-out would result in only a few dozen plants by 2050.\n\nTritium supply is a critical bottleneck. Fusion reactors require tritium, a rare radioactive isotope with a half-life of 12.3 years. Current global tritium production is about 0.5 kg per year, primarily from CANDU reactors. A single 1 GW fusion plant would consume approximately 0.1 kg per day, meaning that even a few plants would exhaust current supplies. Tritium breeding technology\u2014where the reactor produces its own fuel\u2014has not been demonstrated at scale. The DOE's roadmap includes an Integrated Blanket and Fuel Cycle Test Facility to address this, but it is still in the design phase.\n\nCounter-evidence: Some analysts argue that fusion could follow an S-curve adoption similar to solar, which went from niche to mainstream in two decades. However, solar benefited from modularity, declining costs, and policy support. Fusion plants are large, capital-intensive, and face unique regulatory and fuel-supply challenges. The analogy is weak.\n\nNon-electric applications, such as industrial heat and hydrogen production, could provide an earlier market. The DOE's roadmap includes closing the fusion cycle to enable these applications. However, the industrial heat market is smaller than electricity generation, and hydrogen production from fusion would still need to compete with electrolysis powered by cheap renewables.\n\nThe bottom line: fusion will remain a niche energy source for decades. Organizations should not base their energy strategy on fusion becoming a major contributor before 2050. Instead, they should plan for a gradual, long-term transition in which fusion plays a supporting role alongside renewables, fission, and storage.\n\n\n**Key Evidence:**\n\n- The National Academies' pilot plant strategy targets first operation between 2035 and 2040, with a second phase by 2040-2045 (Source 7)\n\n- The DOE's roadmap includes an Integrated Blanket and Fuel Cycle Test Facility to address tritium breeding, still in design phase (Source 14)\n\n- The Fusion Industry Association reports over $9 billion in private investment, but this is a fraction of the estimated $50 billion needed (Source 10)\n\n- The DOE Milestone Program awardees are working on pre-conceptual designs, with milestones due in late 2025 (Source 3)\n\n\n*For corporate strategists, the market outlook means that fusion will not be a significant factor in energy procurement or carbon reduction targets for at least 15-20 years. Companies should focus on near-term decarbonization using available technologies, while maintaining a watching brief on fusion for long-term planning.*"
    },
    {
        "heading": "Strategic Implications: What to Do Now While Waiting for Fusion",
        "body": "**The right strategy is to prepare for fusion's eventual arrival without overcommitting. This means monitoring key milestones, engaging in selective partnerships, and investing in nearer-term clean energy solutions.**\n\nFor energy investors, the key question is not whether fusion will work, but when and at what cost. The evidence suggests that fusion will not be commercially viable before 2040, and its LCOE will be higher than renewables for at least a decade after that. Therefore, near-term investment in fusion as a core energy asset is premature. Instead, investors should focus on companies with strong balance sheets and diversified clean energy portfolios, while allocating a small portion of capital to fusion as a long-term option.\n\nFor corporate strategists, fusion represents a potential long-term source of low-carbon energy, but it should not be the centerpiece of a decarbonization strategy. Companies should set near-term targets based on commercially available technologies: solar, wind, energy efficiency, and in some cases, advanced fission. They should also engage with fusion developers through pilot projects or offtake agreements to gain early access and learning, but without large financial commitments.\n\nFor policy makers, the findings underscore the need for sustained, predictable funding for fusion R&D, as well as regulatory frameworks that can accommodate fusion plants. The U.S. and UK have made progress on fusion-specific regulations, but other countries lag. Policy makers should also support tritium breeding research and supply chain development, as these are critical bottlenecks.\n\nAction steps for organizations: (1) Monitor fusion milestones annually, focusing on SPARC, ITER, and STEP progress. (2) Engage with fusion industry associations and DOE programs to stay informed. (3) Consider small strategic investments in fusion companies with strong technical teams and clear milestones. (4) Do not rely on fusion for near-term carbon reduction; invest in renewables and efficiency now. (5) Prepare for fusion's eventual arrival by developing internal expertise and partnerships.\n\nThe bottom line: fusion is a promising long-term technology, but it is not a near-term solution. The strategic imperative is to balance patience with preparation\u2014investing in what works today while keeping an eye on the horizon.\n\n\n**Key Evidence:**\n\n- The DOE's Fusion Science & Technology Roadmap is contingent on future public-private partnerships and appropriations (Source 13)\n\n- The Fusion Industry Association reports that the industry has created over 4,000 jobs and attracted $9 billion in investment (Source 10)\n\n- The DOE Milestone Program has 8 awardees working on pre-conceptual designs, with initial milestones due in late 2025 (Source 3)\n\n- The National Academies' report recommends a pilot plant between 2035 and 2040, with a second phase by 2040-2045 (Source 7)\n\n\n*The strategic window for fusion is long. Organizations that act now\u2014by building knowledge, forming partnerships, and investing in nearer-term solutions\u2014will be best positioned to capitalize on fusion when it eventually arrives, without overexposing themselves to its risks and delays.*"
    }
]
        },
        "comments": []
    },
    "doc-2222-rejected": {
        "id": "doc-2222-rejected", "title": "Quantum Computing Market Outlook", "version": "1.0",
        "status": "Rejected", "humanStatus": "Needs rewrite", "aiScore": 45, "aiGrade": "Bronze",
        "commentCount": 5, "lastUpdated": "2026-06-29T14:30:00Z", "publishReady": False, "aiReview": None,
        "reportContent": {"brand": "GateX", "label": "Rejected", "date": "2026-06-29", "sections": [{"heading": "Executive Summary", "body": "Mock content for rejected report."}]},
        "comments": []
    },
    "doc-3333-review": {
        "id": "doc-3333-review", "title": "Middle East AI Strategies", "version": "2.1",
        "status": "Needs Human Review", "humanStatus": "Pending Editorial Approval", "aiScore": 85, "aiGrade": "Silver",
        "commentCount": 2, "lastUpdated": "2026-06-30T19:00:00Z", "publishReady": False, "aiReview": None,
        "reportContent": {"brand": "GateX", "label": "Review", "date": "2026-06-30", "sections": [{"heading": "Executive Summary", "body": "Mock content for review report."}]},
        "comments": []
    }
}

@router.get("/", response_model=APIResponse[list])
async def list_reports(
    page: PageParams = Depends(),
    filters: FilterParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List all reports based on filters and pagination.
    Merges in-memory MOCK_REPORTS with actual completed GenerationJobs from the DB.
    """
    from sqlalchemy import select
    from app.models.workflow import GenerationJob
    from app.models.document import Document
    from app.models.enums import JobStatusType

    # Base list from MOCK_REPORTS
    mock_reports_dict = {r["id"]: r for r in MOCK_REPORTS.values() if "id" in r}

    # Fetch completed jobs from DB to persist across server restarts
    stmt = (
        select(GenerationJob, Document)
        .join(Document, GenerationJob.document_id == Document.id)
        .where(GenerationJob.status == JobStatusType.completed)
        .order_by(GenerationJob.started.desc())
    )
    result = await db.execute(stmt)
    
    for job, doc in result.all():
        doc_id = doc.slug or str(doc.id)
        # Avoid overriding if already present in MOCK_REPORTS (it might have richer real-time data)
        if doc_id not in mock_reports_dict:
            mock_reports_dict[doc_id] = {
                "id": doc_id,
                "title": doc.title or job.topic,
                "version": "1.0",
                "status": "Generated",
                "humanStatus": "Pending Review",
                "aiScore": 85,
                "aiGrade": "Silver",
                "commentCount": 0,
                "lastUpdated": (job.completed or job.started).isoformat() + "Z",
                "publishReady": False,
                "aiReview": None,
                "slug": doc.slug,
                "reportContent": {
                    "brand": "GateX",
                    "label": "Deep Research",
                    "date": (job.completed or job.started).strftime("%B %d, %Y"),
                    "sections": [
                        {"heading": "Executive Summary", "body": "Click 'View Report' to load full report content."}
                    ]
                },
                "comments": []
            }
    
    reports_list = list(mock_reports_dict.values())

    # --- Reconcile with persisted GateXPublication records ---
    # This ensures Published status survives server restarts (MOCK_REPORTS is in-memory)
    try:
        from app.models.workflow import GateXPublication
        from sqlalchemy import select
        import hashlib, uuid as _uuid

        stmt = select(
            GateXPublication.document_id,
            GateXPublication.publish_status,
            GateXPublication.external_report_id
        ).where(
            GateXPublication.publish_status.in_(["published", "unpublished"])
        )
        result = await db.execute(stmt)
        pub_rows = result.all()

        # Build a map of doc_uuid -> (publish_status, external_id)
        pub_map = {str(row[0]): (row[1], row[2]) for row in pub_rows}

        for report in reports_list:
            rid = report.get("id", "")
            # Compute UUID that the orchestrator would have used
            try:
                doc_uuid = str(_uuid.UUID(rid))
            except ValueError:
                m = hashlib.md5()
                m.update(rid.encode("utf-8"))
                doc_uuid = str(_uuid.UUID(m.hexdigest()))

            if doc_uuid in pub_map:
                db_status, ext_id = pub_map[doc_uuid]
                if db_status == "published" and report.get("status") != "Published":
                    report["status"] = "Published"
                    report["publishStatus"] = "published"
                    report["externalReportId"] = ext_id
                elif db_status == "unpublished" and report.get("status") not in ("Rejected",):
                    report["status"] = "Rejected"
                    report["publishStatus"] = "unpublished"
    except Exception as _e:
        pass  # DB reconciliation is best-effort; never break the reports list

    # Simple mock filtering to support frontend tabs
    if filters.status:
        reports_list = [r for r in reports_list if r["status"].lower() == filters.status.lower()]

    return success_response(
        data=reports_list,
        message="Fetched mock reports successfully",
        metadata={"total": len(reports_list), "offset": page.offset, "limit": page.limit, "has_more": False}
    )

@router.get("/{document_id}", response_model=APIResponse[dict])
async def get_report_details(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get detailed metadata for a specific report document.
    """
    if document_id in MOCK_REPORTS:
        return success_response(data=MOCK_REPORTS[document_id], message="Fetched report details")
        
    # If not in MOCK_REPORTS, try loading dynamically from R2
    from sqlalchemy import select
    from app.models.document import Document
    from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
    import uuid

    stmt = select(Document).where(Document.slug == document_id)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    
    if not doc:
        try:
            doc_uuid = uuid.UUID(document_id)
            doc = await db.get(Document, doc_uuid)
        except ValueError:
            pass
            
    if doc:
        slug = doc.slug or str(doc.id)
        topic = doc.title or slug
        payload = await _load_report_payload_from_r2(slug, topic)
        entry = _build_mock_report_entry(document_id, topic, slug, payload)
        
        # Cache it in MOCK_REPORTS
        MOCK_REPORTS[document_id] = entry
        MOCK_REPORTS[str(doc.id)] = entry
        if doc.slug:
            MOCK_REPORTS[doc.slug] = entry
            
        return success_response(data=entry, message="Loaded report details from storage")

    # Fallback to a mock report if truly not found
    report = MOCK_REPORTS.get(document_id, MOCK_REPORTS["doc-3333-review"])
    return success_response(data=report, message="Fetched fallback report details")

from pydantic import BaseModel
from typing import Optional

class StatusUpdatePayload(BaseModel):
    status: Optional[str] = None
    humanStatus: Optional[str] = None
    publishReady: Optional[bool] = None

@router.post("/{document_id}/status", response_model=APIResponse[dict])
async def update_report_status(
    document_id: str,
    payload: StatusUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Update the status of a specific report.
    """
    report = MOCK_REPORTS.get(document_id)
    if not report:
        # fallback for new generated jobs
        report = MOCK_REPORTS["doc-3333-review"].copy()
        report["id"] = document_id
        MOCK_REPORTS[document_id] = report
        
    if payload.status is not None:
        report["status"] = payload.status
    if payload.humanStatus is not None:
        report["humanStatus"] = payload.humanStatus
    if payload.publishReady is not None:
        report["publishReady"] = payload.publishReady

    # When a report is explicitly re-approved, clear the "unpublished" DB record
    # so the reconciliation logic does NOT override it back to "Rejected"
    if payload.status in ("Approved", "approved"):
        import uuid, hashlib
        from sqlalchemy import update as sql_update
        from app.models.workflow import GateXPublication
        try:
            try:
                doc_uuid = uuid.UUID(document_id)
            except ValueError:
                m = hashlib.md5()
                m.update(document_id.encode("utf-8"))
                doc_uuid = uuid.UUID(m.hexdigest())
            await db.execute(
                sql_update(GateXPublication)
                .where(GateXPublication.document_id == doc_uuid)
                .where(GateXPublication.publish_status == "unpublished")
                .values(publish_status="re_approved")
            )
            await db.commit()
            # Also clear in-memory markers so it doesn't linger
            report["publishStatus"] = None
            report["externalReportId"] = None
        except Exception:
            pass  # Best-effort — never break the status update
        
    # Removed the legacy 'Needs Revision' full report generation job.
    # Revisions are now handled by the surgical /revise-section endpoint.
        
    return success_response(data=report, message="Report status updated")

class SectionRevisionRequest(BaseModel):
    section_heading: str
    instructions: str

@router.post("/{document_id}/revise-section", response_model=APIResponse[dict])
async def revise_section(
    document_id: str,
    req: SectionRevisionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Surgically revise a single section of the report using AI.
    """
    import httpx
    import os

    if document_id not in MOCK_REPORTS:
        return error_response(message="Report not found")

    report = MOCK_REPORTS[document_id]
    original_text = ""
    target_section = None
    
    for section in report.get("reportContent", {}).get("sections", []):
        if section.get("heading") == req.section_heading:
            original_text = section.get("body", "")
            target_section = section
            break
            
    if not target_section:
        return error_response(message="Section not found in report")

    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY")
    new_text = f"[AI Revision based on: {req.instructions}] {original_text} (simulated update)"

    if api_key:
        try:
            is_groq = "gsk_" in api_key
            url = "https://api.groq.com/openai/v1/chat/completions" if is_groq else "https://api.deepseek.com/chat/completions"
            model = "llama-3.3-70b-versatile" if is_groq else "deepseek-chat"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            prompt = f"Rewrite the following report section based on these instructions from a reviewer:\nReviewer Instructions: {req.instructions}\n\nOriginal Section Text:\n{original_text}"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a professional report editor. Return only the edited text without any conversational filler or quotes. Maintain the professional tone of the report."},
                    {"role": "user", "content": prompt}
                ]
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    new_text = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI API failed: {e}")

    # Update the section
    target_section["body"] = new_text

    # Try to push the updated JSON back to R2 and trigger PDF generation
    try:
        from app.services.generation import _save_report_payload_to_r2
        from app.services.pdf_release import pdf_release_service
        
        slug = report.get("slug") or document_id
        await _save_report_payload_to_r2(slug, report.get("title", ""), report)
        
        # Trigger PDF generation so the preview will have the new text
        await pdf_release_service.get_or_generate_pdf(slug, report, force=True)
    except Exception as e:
        print(f"Failed to sync revised report to R2 or PDF: {e}")

    return success_response(
        data={"edited_text": new_text}, 
        message="Section revised successfully"
    )

@router.get("/{document_id}/download-url", response_model=APIResponse[dict])
async def get_report_download_url(
    document_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Generate a signed URL for secure downloading of report artifacts.
    """
    from app.services.storage import storage_service
    url = await storage_service.get_signed_url(db, file_id)
    if not url:
        return error_response(message="File not found or unauthorized")
    return success_response(data={"url": url}, message="Generated signed URL successfully")

from pydantic import BaseModel
class AIEditRequest(BaseModel):
    documentId: str
    action: str
    paragraphId: str
    text: str

@router.post("/edit", response_model=APIResponse[dict])
async def ai_edit_block(
    req: AIEditRequest,
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Handle AI Rewrite toolbar actions from the frontend for specific document blocks.
    Replaces the exact paragraph text in the mock database.
    """
    import httpx
    import os
    from app.core.config import settings

    doc_id = req.documentId
    if doc_id not in MOCK_REPORTS:
        return error_response(message="Report not found")

    report = MOCK_REPORTS[doc_id]
    original_text = req.text.strip()
    
    # Simple prompt depending on action
    prompt_instruction = "Rewrite the following text."
    if req.action == "expand":
        prompt_instruction = "Expand on the following text, providing more detail and context."
    elif req.action == "rewrite":
        prompt_instruction = "Rewrite the following text to make it more concise and professional."
    elif req.action == "regenerate":
        prompt_instruction = "Completely regenerate the following text, providing a fresh perspective."

    # Call AI (DeepSeek or Groq if available)
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY")
    new_text = f"[AI {req.action.capitalize()}] {original_text} (simulated update)"

    if api_key:
        try:
            is_groq = "gsk_" in api_key
            url = "https://api.groq.com/openai/v1/chat/completions" if is_groq else "https://api.deepseek.com/chat/completions"
            model = "llama-3.3-70b-versatile" if is_groq else "deepseek-chat"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a professional report editor. Return only the edited text without any conversational filler or quotes."},
                    {"role": "user", "content": f"{prompt_instruction}\n\n{original_text}"}
                ]
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=20.0)
                if resp.status_code == 200:
                    data = resp.json()
                    new_text = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI API failed: {e}")

    # Find the paragraph in the report content and replace it
    updated = False
    for section in report.get("reportContent", {}).get("sections", []):
        body = section.get("body", "")
        if original_text in body:
            # Replace the paragraph in the body
            section["body"] = body.replace(original_text, new_text)
            updated = True
            break
            
    if not updated:
        return error_response(message="Could not find the specified text in the report body to edit.")

    return success_response(
        data={"edited_text": new_text}, 
        message=f"AI {req.action} applied successfully"
    )
