import uuid
from typing import Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import Document
from app.models.workflow import WorkflowInstance, WorkflowEvent
from app.models.system import ProcessedEvent
from app.services.audit import audit_service
from app.logging.logger import logger

class WorkflowService:
    @staticmethod
    async def process_workflow_event(
        db: AsyncSession,
        document_id: uuid.UUID,
        event_type: str,
        idempotency_key: str,
        new_state: str,
        actor_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> dict:
        """
        Process a workflow transition in a strict atomic transaction.
        Implements pessimistic locking and idempotency tracking.
        """
        async with db.begin():
            # 1. Idempotency Check
            stmt_check = select(ProcessedEvent).where(ProcessedEvent.idempotency_key == idempotency_key)
            result = await db.execute(stmt_check)
            if result.scalars().first():
                logger.info(f"Idempotent event skipped: {idempotency_key}")
                return {"status": "skipped", "message": "Event already processed"}

            # 2. Lock Document and WorkflowInstance
            stmt_doc = select(Document).where(Document.id == document_id).with_for_update()
            doc_result = await db.execute(stmt_doc)
            document = doc_result.scalars().first()
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            stmt_wf = select(WorkflowInstance).where(WorkflowInstance.document_id == document_id).with_for_update()
            wf_result = await db.execute(stmt_wf)
            workflow = wf_result.scalars().first()

            old_state = workflow.current_state if workflow else "None"
            
            # Transition Validations could be implemented here (e.g. GENERATED -> AI_REVIEWED)
            # For Phase 5, we trust the caller's requested state.
            
            # 3. Update or Create Workflow Instance
            if workflow:
                workflow.current_state = new_state
            else:
                workflow = WorkflowInstance(document_id=document_id, current_state=new_state)
                db.add(workflow)

            # 4. Update Document Status
            document.status = new_state

            # 5. Record Workflow Event
            wf_event = WorkflowEvent(
                workflow_id=workflow.id if workflow.id else uuid.uuid4(), # if new, handle appropriately (usually flush needed)
                previous_state=old_state,
                current_state=new_state,
                actor_id=actor_id,
                event_metadata=metadata or {}
            )
            # If it's a brand new workflow instance, we need to flush to get the ID
            if not workflow.id:
                await db.flush()
                wf_event.workflow_id = workflow.id
                
            db.add(wf_event)

            # 6. Audit Logging
            await audit_service.log_action(
                db=db,
                table_name="documents",
                record_id=document_id,
                action="workflow_transition",
                old_data={"status": old_state},
                new_data={"status": new_state},
                changed_by=actor_id
            )

            # 7. Mark event as processed (Idempotency)
            processed_event = ProcessedEvent(idempotency_key=idempotency_key, event_type=event_type)
            db.add(processed_event)
            
            # The context manager (db.begin()) commits all changes atomically!
            logger.info(f"Workflow {document_id} transitioned from {old_state} to {new_state}")
            return {"status": "success", "message": "Workflow transitioned successfully", "new_state": new_state}

workflow_service = WorkflowService()
