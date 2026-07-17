from prometheus_client import Counter, Histogram, Gauge

knowledge_uploads_total = Counter(
    "knowledge_uploads_total",
    "Total number of knowledge document uploads",
    ["collection_id", "file_type"]
)

knowledge_processing_duration_seconds = Histogram(
    "knowledge_processing_duration_seconds",
    "Time taken for knowledge document processing stages",
    ["stage"]
)

knowledge_retrieval_latency_ms = Histogram(
    "knowledge_retrieval_latency_ms",
    "Latency of knowledge retrieval queries in milliseconds"
)

knowledge_cache_hits_total = Counter(
    "knowledge_cache_hits_total",
    "Total number of knowledge cache hits",
    ["cache_type"]
)

rag_generation_requests_total = Counter(
    "rag_generation_requests_total",
    "Total number of RAG generation requests",
    ["rag_enabled"]
)

knowledge_validation_confidence = Gauge(
    "knowledge_validation_confidence",
    "Most recent average validation confidence score"
)
