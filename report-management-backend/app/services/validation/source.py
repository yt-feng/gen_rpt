import uuid
from typing import Dict, Any, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.knowledge import KnowledgeDocument, KnowledgeCollection
from app.models.validation import ValidationPolicy

class SourceValidationService:
    async def validate_sources(
        self,
        db: AsyncSession,
        document_ids: List[uuid.UUID],
        policy: ValidationPolicy
    ) -> Tuple[Dict[uuid.UUID, Dict[str, Any]], List[str]]:
        """
        Validates all retrieved sources based on policy guidelines.
        Returns:
            - A dict mapping document_id to its validation details/reasons.
            - A list of errors/warnings encountered during validation.
        """
        errors = []
        validation_results = {}
        
        if not document_ids:
            return validation_results, errors

        # Query all documents with their collection and sources loaded
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.id.in_(document_ids),
            KnowledgeDocument.deleted_at.is_(None)
        ).options(
            selectinload(KnowledgeDocument.collection),
            selectinload(KnowledgeDocument.sources)
        )
        
        res = await db.execute(stmt)
        documents = res.scalars().all()
        
        allowed_types = (policy.rules or {}).get("allowed_source_types", [])
        
        for doc in documents:
            doc_id = doc.id
            is_valid = True
            reasons = []
            
            # 1. Processing Status check
            if doc.processing_status != "completed":
                is_valid = False
                reasons.append(f"Invalid processing status: {doc.processing_status}")
                
            # 2. Collection Status check
            if not doc.collection or doc.collection.status != "active":
                is_valid = False
                reasons.append(f"Collection status is not active: {doc.collection.status if doc.collection else 'None'}")
                
            # 3. Document validation status check (reject failed validation)
            if doc.validation_status == "failed":
                is_valid = False
                reasons.append("Document validation status is failed")
                
            # 4. Source type & publisher verification
            source_type = "unknown"
            publisher = None
            if doc.sources:
                primary_source = doc.sources[0]
                source_type = primary_source.source_type
                publisher = primary_source.publisher
                
                # Check source type against allowed types in policy rules
                if allowed_types and source_type not in allowed_types:
                    is_valid = False
                    reasons.append(f"Source type '{source_type}' is not allowed by policy")
            else:
                if allowed_types and "unknown" not in allowed_types:
                    is_valid = False
                    reasons.append("No source metadata found and 'unknown' is not allowed")

            validation_results[doc_id] = {
                "document_id": doc_id,
                "file_name": doc.file_name,
                "is_valid": is_valid,
                "source_type": source_type,
                "publisher": publisher,
                "processing_status": doc.processing_status,
                "validation_status": doc.validation_status,
                "reasons": reasons
            }
            
            if not is_valid:
                errors.append(f"Document {doc.file_name} rejected: {', '.join(reasons)}")

        # Add stubs for missing document IDs
        found_ids = {doc.id for doc in documents}
        for doc_id in document_ids:
            if doc_id not in found_ids:
                validation_results[doc_id] = {
                    "document_id": doc_id,
                    "file_name": "Missing Document",
                    "is_valid": False,
                    "source_type": "unknown",
                    "publisher": None,
                    "reasons": ["Document does not exist or has been soft-deleted"]
                }
                errors.append(f"Document ID {doc_id} rejected: Not found in database")
                
        return validation_results, errors

source_validation_service = SourceValidationService()
