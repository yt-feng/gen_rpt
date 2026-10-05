"""Explicit, source-free overview fallback for unattended report generation.

Failed research drafts are never reused. An overview is a different deliverable,
with its own disclosure and receipt, not a claim to have fulfilled private RAG.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

import requests

from .deepseek_client import EditorialServiceExhausted, EditorialFormatContractError
from .web_report_pipeline import ReportQualityError


def availability_reason(error):
    if isinstance(error, EditorialServiceExhausted):
        return "model_temporarily_unavailable"
    if isinstance(error, requests.ConnectionError):
        return "model_temporarily_unavailable"
    if isinstance(error, requests.Timeout):
        return "model_timeout"
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        if status == 402:
            return "model_balance_unavailable"
        if status in {401, 403}:
            return "model_auth_rejected"
        if status == 429 or status in {408, 500, 502, 503, 504}:
            return "model_temporarily_unavailable"
    return None


def template_overview(topic):
    """Useful questions and method, without asserting facts about the topic."""
    report = {
        "title": f"{topic}: a practical orientation guide",
        "intro": [f"This guide is a starting point for exploring {topic}. It explains how to frame the subject, compare possible approaches, and identify information worth checking. It is a general editorial overview, not a finding from private documents or a current market assessment."],
        "sections": [
            {"title": "Clarify the question before comparing answers", "paragraphs": [
                f"Start by stating what you want to understand about {topic}. A question about how something works calls for definitions and mechanisms. A question about whether to adopt an approach calls for alternatives, constraints, and evidence of suitability. Keeping these questions separate makes a discussion easier to follow.",
                "Write down the intended audience, the decision to be made, and the boundaries of the discussion. Define unfamiliar terms in plain language. Treat the wording of a topic as a question to investigate rather than as proof that its assumptions are true. This is especially useful when the topic contains a forecast or a strong conclusion."]},
            {"title": "Compare approaches using a consistent frame", "paragraphs": [
                "Describe each possible approach in terms of its purpose, inputs, operating requirements, and limitations. Ask what would have to be available for it to work, which dependencies would remain outside your control, and what a simpler alternative could accomplish. Keep the comparison focused on the same problem and audience.",
                "Separate the expected benefit from the conditions needed to obtain it. A proposal can be attractive in principle while still needing evidence of feasibility in a particular setting. Record assumptions explicitly and consider how the decision would change if an assumption did not hold. Avoid treating promotional language as an observed outcome."]},
            {"title": "Build an evidence checklist for the next decision", "paragraphs": [
                "For factual follow-up, seek original documentation, clearly defined measurements, and records that explain their methods. Check the publication date and whether the material applies to the place, use case, and period under discussion. A statement can be accurate in its original setting without answering your particular question.",
                "Keep a short list of what is known, what is assumed, and what still needs checking. If a conclusion depends on a price, legal requirement, technical specification, or recent event, verify it from an appropriate current source before relying on it. This overview supplies a research structure; it does not supply verified values or citations."]},
            {"title": "Turn the overview into a useful next step", "paragraphs": [
                "Choose a bounded question that can be answered with the information available. Write a brief comparison, identify the evidence that would change your view, and decide who needs to review it. If the evidence is incomplete, document the open question instead of replacing the gap with an estimate presented as fact.",
                "Revisit the outline when new material becomes available. Retain definitions and decision criteria that remain useful, update claims that depend on changing conditions, and remove conclusions that the evidence no longer supports. A clear, maintainable explanation is more useful than a long document with an uncertain factual basis."]},
        ],
        "faq": [
            {"question": "What does this overview establish?", "answer": "It provides a way to organize questions and compare approaches. It does not establish current facts, verify the premise of the topic, or summarize unavailable private documents."},
            {"question": "How should I use it?", "answer": "Use the sections as a reading and research checklist. Add verified source material when making a factual claim or applying the discussion to a specific decision."},
        ],
    }
    profile, sections, faq = topic_framework(topic)
    report["sections"][:2] = sections
    report["faq"] = faq + report["faq"]
    report["overview_profile"] = profile
    return report


def topic_framework(topic):
    text = topic.lower()
    if re.search(r"trade|e.?commerce|retail|logistic|export|import|supply.chain|贸易|电商", text):
        return "trade_commerce", [
            {"title": "Map the route from discovery to fulfilment", "paragraphs": [
                "For trade and commerce questions, separate how a buyer discovers an offer from how the order is paid for, delivered, and supported. These are related but different parts of a customer journey. A channel that attracts attention still needs a workable fulfilment process and a clear policy for returns or disputes.",
                "Build a simple journey map: buyer need, offer, sales channel, payment, delivery, and after-sales support. At each stage, ask who performs the work and who bears the cost. Use this map to locate unanswered questions before comparing platforms, distributors, or direct sales approaches."]},
            {"title": "Distinguish commercial access from operating economics", "paragraphs": [
                "A useful comparison separates access to customers from the economics of serving them. Consider product suitability, channel responsibilities, shipping constraints, inventory exposure, and the work required to resolve a failed order. Treat sales projections as assumptions until supported by evidence from the relevant market and channel.",
                "For cross-border activity, add a checklist covering classification, documentation, permitted claims, destination requirements, and the parties responsible for compliance. The applicable requirements depend on the product and jurisdiction; verify them from current official sources. This guide does not assert a tariff, tax rate, delivery time, or market size."]},
        ], [{"question": "How can I compare a marketplace with direct sales?", "answer": "Compare customer discovery, control of the relationship, fulfilment responsibilities, payment handling, and the cost categories that each approach creates. Use the same product and service assumptions for both."}]
    if re.search(r"econom|market|invest|finance|growth|inflation|equity|bond|经济|市场|投资", text):
        return "economy_market", [
            {"title": "Separate economic mechanisms from market forecasts", "paragraphs": [
                "Economic and market questions are easier to frame when the mechanism is separated from the prediction. Start with the relevant actors: households, businesses, governments, or investors. Ask which incentives, constraints, and transactions connect them. A plausible mechanism can guide research without proving the size or timing of an outcome.",
                "Define what the word market means in the discussion. It might refer to customer demand, an industry, a location, or a traded asset. Keep these meanings separate when choosing evidence. Explain whether the question concerns activity, prices, profitability, access, or distribution, since these measures answer different questions."]},
            {"title": "Use scenarios to expose assumptions", "paragraphs": [
                "A qualitative scenario comparison can make a decision more transparent without inventing a numerical forecast. Describe the conditions that would support an outcome, the constraints that could prevent it, and the observations that would change the interpretation. Present scenarios as conditional possibilities rather than as measured probabilities.",
                "When reviewing a market claim, check the population, geography, period, units, and definitions behind it. Distinguish a change in nominal value from a change in activity, and distinguish a forecast from an observation. Do not combine incompatible estimates merely because they share a label. Current values require verification from the underlying publication."]},
        ], [{"question": "What can a qualitative market overview tell me?", "answer": "It can explain a mechanism, compare assumptions, and identify useful evidence to seek. It cannot establish a current valuation, return expectation, or forecast without relevant verified data."}]
    if re.search(r"technolog|software|hardware|comput|\bai\b|quantum|fusion|energy|climate|robot|infrastructure|技术|算力|人工智能", text):
        return "technology", [
            {"title": "Describe the system before judging a capability", "paragraphs": [
                "For a technology question, distinguish the intended function from the implementation. Describe the inputs, the transformation being performed, the outputs, and the conditions in which the system would operate. This creates a practical boundary for evaluating a claim without assuming that a demonstration represents routine performance.",
                "Map the surrounding dependencies: data or materials, supporting infrastructure, integration interfaces, operator skills, and maintenance. Ask which dependencies are part of the proposed solution and which must be supplied separately. This helps a reader compare complete operating approaches instead of isolated headline features."]},
            {"title": "Connect evaluation to a concrete use case", "paragraphs": [
                "Choose evaluation questions that follow from the use case. These might concern reliability, interoperability, resource use, recovery after a failure, or the effort needed to maintain the system. Define the conditions under which a comparison would be meaningful. Do not infer a specification or deployment readiness from the topic alone.",
                "Separate a concept, a demonstration, a controlled pilot, and a sustained operation when reading supporting material. Ask what was tested, what remained outside the test, and how the result would transfer to the intended setting. Treat current performance figures, product claims, and availability statements as items to verify from original technical documentation."]},
        ], [{"question": "How should I compare two technical approaches?", "answer": "Use the same intended task, operating conditions, integration boundary, and evaluation questions. Identify dependencies and limitations before drawing a conclusion from a claimed capability."}]
    return "general", report_framework_sections(), []


def report_framework_sections():
    # General subjects still get a usable inquiry structure rather than claims
    # borrowed from an unrelated industry.
    return [
        {"title": "Frame the subject as an answerable question", "paragraphs": [
            "Identify the purpose of the discussion and the audience it should help. Separate a request for a definition from a request to compare options or interpret an event. Write down the assumptions contained in the topic and keep them open to examination rather than treating them as established facts.",
            "Define the boundaries of the subject in plain language. Identify the terms that may have different meanings for different readers. Explain what would count as a useful answer and which questions remain outside the scope. A clear boundary makes later evidence gathering and discussion more focused."]},
        {"title": "Compare explanations and possible next steps", "paragraphs": [
            "Describe plausible approaches using consistent criteria. Ask what each approach is intended to accomplish, which inputs it requires, what constraints apply, and what would indicate that it is unsuitable. Make the reasoning visible so a reader can distinguish a conditional suggestion from a factual conclusion.",
            "Record the open questions that would change the comparison. Prefer a small, answerable inquiry over a broad conclusion with an unclear basis. When a claim depends on current circumstances, seek the original source and check its relevance before relying on it. Leave missing information visible instead of filling it with an invented detail."]},
    ]


def validate_overview(value):
    if not isinstance(value, dict) or not isinstance(value.get("title"), str):
        return False
    if not isinstance(value.get("intro"), list) or not isinstance(value.get("sections"), list):
        return False
    if len(value["sections"]) < 3 or len(value.get("faq", [])) < 2:
        return False
    texts = [value["title"]] + list(value["intro"])
    for section in value["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("paragraphs"), list):
            return False
        texts += [section.get("title")] + section["paragraphs"]
    for item in value["faq"]:
        if not isinstance(item, dict):
            return False
        texts += [item.get("question"), item.get("answer")]
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        return False
    body = " ".join(texts)
    # No invented measurements, quotations, links or citation markers. Facts
    # requiring named attribution belong in the grounded research deliverable.
    forbidden = r"\d|https?://|www\.|\[[^\]]+\]|<[^>]+>|according to|stud(?:y|ies) (?:show|found)|\b(?:million|billion|trillion|percent)\b"
    return 250 <= len(body.split()) <= 1800 and not re.search(forbidden, body, re.I)


def write_overview(output_dir, topic, client, reason):
    report = None
    trigger_reason = reason
    if client is not None:
        try:
            candidate = client.chat_json([
                {"role": "system", "content": "Write an English general conceptual overview for discovery and reading. Explain concepts, qualitative trade-offs, useful questions and practical next steps. Do not report current events or private-document findings. No statistics, measurements, numbers, forecasts, named source attributions, quotations, citations, links or HTML. Do not affirm an unverified premise from the topic. Return JSON: title:string, intro:[string], sections:[{title:string,paragraphs:[string]}], faq:[{question:string,answer:string}]. Provide at least three substantive sections, two FAQ answers and about six hundred words. The document is a general overview, not sourced research."},
                {"role": "user", "content": topic},
            ], temperature=0.2, max_tokens=2600)
            if validate_overview(candidate):
                report = candidate
            else:
                reason = "overview_response_unusable"
        except (json.JSONDecodeError, EditorialFormatContractError):
            reason = "overview_response_unusable"
        except Exception as exc:
            external = availability_reason(exc)
            if external is None:
                raise
            reason = external
    if report is None:
        report = template_overview(topic)
        mode = "editorial_template"
    else:
        mode = "model_overview"
    # The trusted template repeats the caller's topic as text, never as a fact.
    report.update({"references": [], "exhibits": [], "content_mode": "general_overview",
                   "generation_outcome": {"status": "generated_degraded", "reason": reason,
                                          "overview_method": mode, "trigger_reason": trigger_reason, "evidence_verified": False}})
    disclosure = "General overview: conceptual guidance and questions for further research. No private-document findings, verified current data or source citations are claimed."
    report["disclaimer"] = disclosure
    output_dir.mkdir(parents=True, exist_ok=True)
    esc = html.escape
    body = f"<h1>{esc(report['title'])}</h1><p class='disclosure'>{disclosure}</p>"
    md = f"# {report['title']}\n\n{disclosure}\n\n"
    for paragraph in report["intro"]:
        body += f"<p>{esc(paragraph)}</p>"
        md += paragraph + "\n\n"
    for section in report["sections"]:
        body += f"<h2>{esc(section['title'])}</h2>"
        md += f"## {section['title']}\n\n"
        for paragraph in section["paragraphs"]:
            body += f"<p>{esc(paragraph)}</p>"
            md += paragraph + "\n\n"
    body += "<h2>Frequently asked questions</h2>"
    for item in report["faq"]:
        body += f"<h3>{esc(item['question'])}</h3><p>{esc(item['answer'])}</p>"
        md += f"### {item['question']}\n\n{item['answer']}\n\n"
    structured = json.dumps({"@context": "https://schema.org", "@type": "Article", "headline": report["title"], "description": disclosure}, ensure_ascii=False).replace("<", "\\u003c")
    page = f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(report['title'])}</title><meta name='description' content='{esc(disclosure, quote=True)}'><meta name='robots' content='index,follow'><script type='application/ld+json'>{structured}</script><style>body{{max-width:760px;margin:40px auto;padding:0 24px;font:18px/1.7 system-ui;color:#152238}}h1,h2,h3{{line-height:1.3}}.disclosure{{color:#52647a}}</style></head><body><article>{body}</article></body></html>"
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    (output_dir / "report.html").write_text(page, encoding="utf-8")
    (output_dir / "report.md").write_text(md, encoding="utf-8")
    return report


def run_continuity(*, args, output_dir, slug, fetch_rag, client_factory, pipeline_factory):
    if not str(args.topic).strip():
        raise ValueError("A topic is required for a general overview")
    if args.source_dir or args.source_mode not in (None, "web_only"):
        raise ValueError("seo_overview is only available for the unattended web workflow")
    if args.language.lower() != "en":
        raise ValueError("seo_overview currently requires --language en")
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff.-]{0,180}", slug):
        raise ValueError("Invalid report slug")
    requested_rag = str(os.getenv("RAG_REQUIRED", "")).lower() in {"true", "1", "yes", "on"}
    package, reason = None, "no_validated_context"
    backend, token = os.getenv("BACKEND_URL"), os.getenv("INTERNAL_TOKEN")
    if backend and token and os.getenv("GENERATION_CONTEXT_STATE") != "overview_only":
        try:
            package = fetch_rag(slug, backend, token, args.topic)
        except RuntimeError as exc:
            cause = exc.__cause__
            if isinstance(cause, requests.HTTPError) and cause.response is not None and cause.response.status_code in {401, 403}:
                raise
            if cause is None or not (isinstance(cause, requests.Timeout) or availability_reason(cause)):
                raise
            reason = "context_temporarily_unavailable"
    try:
        client = client_factory(model=args.model)
    except ValueError as exc:
        if not str(exc).startswith("Missing DEEPSEEK_API_KEY."):
            raise
        client, reason = None, "model_not_configured"
    with TemporaryDirectory(prefix="gen-rpt-continuity-") as temporary:
        staged = Path(temporary) / "report"
        report = None
        if package is not None and client is not None:
            try:
                result = pipeline_factory(client=client, language="en").build_report(
                    topic=args.topic, output_dir=staged, rag_context=package.context_text,
                    rag_sources=package.sources, rag_required=requested_rag,
                    source_mode="web_only", checkpoint_path=args.checkpoint_path)
                report = result["report"]
                report["generation_outcome"] = {"status": "generated", "reason": "grounded_report", "evidence_verified": True}
            except ReportQualityError:
                reason = "research_quality_downgraded"
            except Exception as exc:
                reason = availability_reason(exc)
                if reason is None:
                    raise
        if report is None:
            # Never carry rejected drafts, extracted private text or stale assets
            # into the independent overview publication directory.
            if staged.exists():
                shutil.rmtree(staged)
            report = write_overview(staged, args.topic, client, reason)
        outcome = report["generation_outcome"]
        outcome.update({"content_policy": "seo_overview", "requested_rag_required": requested_rag,
                        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
                        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "1"),
                        "job_id": os.getenv("GENERATION_JOB_ID", ""),
                        "job_retry_count": os.getenv("GENERATION_JOB_RETRY_COUNT", ""),
                        "slug": slug, "report_id": output_dir.name})
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (staged / "web_report_payload.json").write_text(payload, encoding="utf-8")
        # The uploader expects report.html; the normal renderer writes index.html.
        shutil.copyfile(staged / "index.html", staged / "report.html")
        if not (staged / "report.md").read_text(encoding="utf-8").strip():
            raise RuntimeError("Generated report is empty")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        shutil.copytree(staged, output_dir)
    receipt = {**outcome, "report_dir": str(output_dir),
               "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    for name in ("report.html", "report.md"):
        receipt[name + "_sha256"] = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
    result_path = args.result_path or Path(".artifacts/report-generation/result.json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key in ("report_dir", "report_id", "slug", "status", "reason"):
                stream.write(f"{key}={receipt[key]}\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"## Generation result\n- Outcome: {receipt['status']}\n- Reason: {receipt['reason']}\n- Content policy: seo_overview\n- Requested private evidence: {requested_rag}\n- Output: `{output_dir}`\n")
    print(f"Report ready: {receipt['status']} ({receipt['reason']}); receipt: {result_path}")
    return receipt
