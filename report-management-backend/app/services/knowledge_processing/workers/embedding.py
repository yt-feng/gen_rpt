import time
import hashlib
import asyncio
import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any
from openai import AsyncOpenAI, RateLimitError, AuthenticationError
from app.core.config import settings

logger = structlog.get_logger("report_management")

async def generate_chunk_embeddings(
    chunks: List[Dict[str, Any]], 
    model: str = "text-embedding-3-small"
) -> List[Dict[str, Any]]:
    start_time = time.time()
    processed_embeddings = []
    
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "REPLACE_WITH_REAL_VALUE":
        raise ValueError("OPENAI_API_KEY is not configured or contains placeholder.")
        
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    texts = [chunk["content"] for chunk in chunks]
    
    batch_size = 100
    all_vectors = []
    
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        
        retries = settings.KNOWLEDGE_RETRY_COUNT if hasattr(settings, "KNOWLEDGE_RETRY_COUNT") else 3
        delay = 2.0
        
        for attempt in range(retries):
            try:
                response = await client.embeddings.create(
                    input=batch_texts,
                    model=model
                )
                batch_vectors = [data.embedding for data in response.data]
                all_vectors.extend(batch_vectors)
                break
            except RateLimitError as e:
                if attempt == retries - 1:
                    logger.error("Rate limit exceeded and max retries reached", error=str(e))
                    raise
                logger.warn(f"OpenAI Rate limit hit. Retrying in {delay}s...", attempt=attempt + 1)
                await asyncio.sleep(delay)
                delay *= 2
            except AuthenticationError as e:
                logger.error("OpenAI Authentication error. Invalid API key.", error=str(e))
                raise
                
    for idx, chunk in enumerate(chunks):
        vector = all_vectors[idx]
        dimension = len(vector)
        
        elapsed = time.time() - start_time
        latency = elapsed / len(chunks) if chunks else 0.0
        
        processed_embeddings.append({
            "chunk_id": chunk.get("id"),
            "chunk_number": chunk.get("chunk_number"),
            "embedding_model": model,
            "embedding_version": "1.0.0",
            "dimension": dimension,
            "status": "completed",
            "generated_time": datetime.now(timezone.utc),
            "provider": "openai",
            "latency": round(latency, 4),
            "vector": vector,
            "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
        })
        
    return processed_embeddings


async def generate_query_embedding(
    query: str, 
    model: str = "text-embedding-3-small"
) -> List[float]:
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "REPLACE_WITH_REAL_VALUE":
        raise ValueError("OPENAI_API_KEY is not configured or contains placeholder.")
        
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(
        input=[query],
        model=model
    )
    return response.data[0].embedding
