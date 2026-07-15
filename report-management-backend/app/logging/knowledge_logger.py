import logging
from app.core.config import settings

# Base logger for knowledge intelligence
base_logger = logging.getLogger("report_management.knowledge")
# Configure log level from settings
log_level = getattr(logging, settings.KNOWLEDGE_LOG_LEVEL.upper(), logging.INFO)
base_logger.setLevel(log_level)

# Domain-specific sub-loggers
knowledge_api_logger = logging.getLogger("report_management.knowledge.api")
knowledge_processing_logger = logging.getLogger("report_management.knowledge.processing")
knowledge_retrieval_logger = logging.getLogger("report_management.knowledge.retrieval")
knowledge_validation_logger = logging.getLogger("report_management.knowledge.validation")
knowledge_workers_logger = logging.getLogger("report_management.knowledge.workers")
knowledge_errors_logger = logging.getLogger("report_management.knowledge.errors")
knowledge_performance_logger = logging.getLogger("report_management.knowledge.performance")
knowledge_health_logger = logging.getLogger("report_management.knowledge.health")
