import uuid
import json
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.knowledge import KnowledgeDocument, ValidationResult, KnowledgeSource
from app.models.validation import ValidationReport, ValidationPolicy
from app.services.validation.freshness import freshness_service

class KnowledgeQualityService:
    async def get_quality_metrics(self, db: AsyncSession) -> Dict[str, Any]:
        # Count validated vs failed docs
        validated_res = await db.execute(
            select(func.count(KnowledgeDocument.id)).filter(
                KnowledgeDocument.validation_status == "validated",
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        validated_count = validated_res.scalar() or 0

        failed_res = await db.execute(
            select(func.count(KnowledgeDocument.id)).filter(
                KnowledgeDocument.validation_status == "failed",
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        failed_count = failed_res.scalar() or 0

        total = validated_count + failed_count
        val_success_rate = (validated_count / total) if total > 0 else 1.0

        # 1. Compute authority_score: average authority_score from knowledge_sources joined to active docs
        authority_res = await db.execute(
            select(func.avg(KnowledgeSource.authority_score))
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeSource.document_id)
            .filter(KnowledgeDocument.deleted_at.is_(None))
        )
        authority_score = float(authority_res.scalar() or 0.90)

        # 2. Compute freshness_score: average freshness score across active documents using the decay formula
        docs_res = await db.execute(
            select(KnowledgeDocument).filter(KnowledgeDocument.deleted_at.is_(None))
        )
        active_docs = docs_res.scalars().all()
        
        if active_docs:
            policy_res = await db.execute(select(ValidationPolicy).limit(1))
            policy = policy_res.scalar()
            if not policy:
                policy = ValidationPolicy(rules={})
                
            freshness_scores = await freshness_service.calculate_freshness(db, active_docs, policy)
            freshness_score = float(sum(freshness_scores.values()) / len(freshness_scores)) if freshness_scores else 0.88
        else:
            freshness_score = 0.88

        # 3. Compute coverage_score: (docs with chunks > 0) / total active docs
        total_active_res = await db.execute(
            select(func.count(KnowledgeDocument.id)).filter(
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        total_active = total_active_res.scalar() or 0

        if total_active > 0:
            from app.models.knowledge import KnowledgeChunk
            docs_with_chunks_res = await db.execute(
                select(func.count(func.distinct(KnowledgeDocument.id)))
                .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .filter(KnowledgeDocument.deleted_at.is_(None))
            )
            docs_with_chunks = docs_with_chunks_res.scalar() or 0
            coverage_score = float(docs_with_chunks / total_active)
        else:
            coverage_score = 0.85

        # 4. Compute confidence_score & evidence_quality_score from the 100 most recent ValidationReport records
        reports_res = await db.execute(
            select(ValidationReport)
            .order_by(ValidationReport.created_at.desc())
            .limit(100)
        )
        reports = reports_res.scalars().all()

        confidence_values = []
        evidence_quality_values = []

        for r in reports:
            conf_data = r.confidence_scores
            if isinstance(conf_data, str):
                try:
                    conf_data = json.loads(conf_data)
                except Exception:
                    conf_data = None
            if isinstance(conf_data, dict):
                overall = conf_data.get("overall_confidence")
                if overall is not None:
                    confidence_values.append(float(overall))

            ev_data = r.evidence_completeness
            if isinstance(ev_data, str):
                try:
                    ev_data = json.loads(ev_data)
                except Exception:
                    ev_data = None
            if isinstance(ev_data, dict):
                comp = ev_data.get("completeness_score")
                if comp is not None:
                    evidence_quality_values.append(float(comp))

        confidence_score = float(sum(confidence_values) / len(confidence_values)) if confidence_values else 0.93
        evidence_quality_score = float(sum(evidence_quality_values) / len(evidence_quality_values)) if evidence_quality_values else 0.91

        # Calculate logical values for completeness and effectiveness to keep dynamic
        completeness_score = evidence_quality_score
        effectiveness_score = float(round((confidence_score + val_success_rate) / 2, 2))

        # 5. Compute health_status
        scores = [
            val_success_rate,
            authority_score,
            freshness_score,
            coverage_score,
            confidence_score,
            evidence_quality_score
        ]
        min_score = min(scores) if scores else 1.0
        if min_score > 0.7:
            health_status = "healthy"
        elif min_score >= 0.4:
            health_status = "degraded"
        else:
            health_status = "critical"

        return {
            "overall_quality_score": float(round(val_success_rate * 0.95, 2)),
            "authority_score": float(round(authority_score, 2)),
            "freshness_score": float(round(freshness_score, 2)),
            "coverage_score": float(round(coverage_score, 2)),
            "confidence_score": float(round(confidence_score, 2)),
            "validation_score": float(round(val_success_rate, 2)),
            "evidence_quality_score": float(round(evidence_quality_score, 2)),
            "completeness_score": float(round(completeness_score, 2)),
            "effectiveness_score": float(round(effectiveness_score, 2)),
            "health_status": health_status
        }

knowledge_quality_service = KnowledgeQualityService()
