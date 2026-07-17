import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.knowledge import KnowledgeProcessingQueue
from app.services.knowledge_processing.engine import KnowledgeProcessingEngine
from app.core.config import settings
from app.logging.logger import logger

class KnowledgeProcessingPipeline:
    def __init__(self):
        self.active = False
        self._task = None
        self.engine = KnowledgeProcessingEngine()

    def start(self):
        """Starts the asynchronous processing polling loop."""
        if self.active:
            return
        self.active = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Knowledge Processing Pipeline background loop started.")

    def stop(self):
        """Stops the asynchronous processing loop."""
        if not self.active:
            return
        self.active = False
        if self._task:
            self._task.cancel()
            logger.info("Knowledge Processing Pipeline background loop stopped.")

    async def _loop(self):
        while self.active:
            try:
                # Check feature flags
                if not settings.KNOWLEDGE_ENABLED or not settings.PROCESSING_ENABLED:
                    await asyncio.sleep(5)
                    continue

                # Query for a pending or retry job
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(KnowledgeProcessingQueue)
                        .filter(KnowledgeProcessingQueue.status.in_(["pending", "retry"]))
                        .order_by(
                            KnowledgeProcessingQueue.priority.desc(),
                            KnowledgeProcessingQueue.created_at.asc()
                        )
                        .limit(1)
                    )
                    job = result.scalars().first()
                    
                    if job:
                        job_id = job.id
                        logger.info(f"Pipeline picked up job: {job_id} for document: {job.document_id}")
                        
                        # Process the job
                        # The engine will commit its own transactions internally
                        await self.engine.process_document_job(db, job_id)
                        
                        # Yield control to prevent event loop starvation
                        await asyncio.sleep(0.1)
                        continue

                # No job found, sleep before next poll
                await asyncio.sleep(2.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Knowledge Processing Pipeline loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)

# Global pipeline instance
knowledge_pipeline = KnowledgeProcessingPipeline()
