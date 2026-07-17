import time
import hashlib
import asyncio
import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any
from app.core.config import settings

logger = structlog.get_logger("report_management")


async def _embed_via_huggingface(texts: List[str], model: str) -> List[List[float]]:
    """
    Call the Hugging Face Inference API to get embeddings.
    Model: BAAI/bge-small-en-v1.5 (384 dims) or similar.
    """
    import httpx
    url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{model}"
    headers = {
        "Authorization": f"Bearer {settings.HF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    batch_size = 64
    all_vectors: List[List[float]] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            retries = getattr(settings, "KNOWLEDGE_RETRY_COUNT", 3)
            delay = 2.0
            for attempt in range(retries):
                try:
                    resp = await client.post(url, headers=headers, json={"inputs": batch, "options": {"wait_for_model": True}})
                    resp.raise_for_status()
                    batch_vectors = resp.json()
                    if isinstance(batch_vectors[0], list):
                        all_vectors.extend(batch_vectors)
                    else:
                        all_vectors.append(batch_vectors)
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 503 and attempt < retries - 1:
                        wait = int(e.response.headers.get("X-Wait-For-Model", str(int(delay * 10)))) / 10
                        logger.warning(f"HF model loading, retrying in {wait}s...", attempt=attempt + 1)
                        await asyncio.sleep(wait)
                    elif attempt == retries - 1:
                        logger.error("HF embedding request failed after max retries", error=str(e))
                        raise
                    else:
                        await asyncio.sleep(delay)
                        delay *= 2
                except Exception as e:
                    if attempt == retries - 1:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2

    return all_vectors


async def _embed_via_openai(texts: List[str], model: str) -> List[List[float]]:
    """
    Call OpenAI Embeddings API (fallback when HF_API_TOKEN is not set).
    """
    from openai import AsyncOpenAI, RateLimitError, AuthenticationError

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    batch_size = 100
    all_vectors: List[List[float]] = []

    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        retries = getattr(settings, "KNOWLEDGE_RETRY_COUNT", 3)
        delay = 2.0

        for attempt in range(retries):
            try:
                response = await client.embeddings.create(input=batch_texts, model=model)
                all_vectors.extend([data.embedding for data in response.data])
                break
            except RateLimitError as e:
                if attempt == retries - 1:
                    logger.error("OpenAI rate limit exceeded and max retries reached", error=str(e))
                    raise
                logger.warning(f"OpenAI rate limit hit, retrying in {delay}s...", attempt=attempt + 1)
                await asyncio.sleep(delay)
                delay *= 2
            except AuthenticationError as e:
                logger.error("OpenAI authentication error — invalid API key.", error=str(e))
                raise

    return all_vectors


async def generate_chunk_embeddings(
    chunks: List[Dict[str, Any]],
    model: str = None
) -> List[Dict[str, Any]]:
    """
    Generate embeddings for a list of chunk dicts.
    Provider resolution:
      1. Hugging Face Inference API (if HF_API_TOKEN is set) — free, 384 dims
      2. OpenAI Embeddings API (if OPENAI_API_KEY is set) — paid, 1536 dims
    """
    if model is None:
        model = settings.KNOWLEDGE_EMBEDDING_MODEL

    start_time = time.time()

    use_hf = bool(settings.HF_API_TOKEN and settings.HF_API_TOKEN.strip())
    use_openai = bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY not in ("", "REPLACE_WITH_REAL_VALUE"))

    if not use_hf and not use_openai:
        raise ValueError(
            "No embedding provider configured. "
            "Set HF_API_TOKEN (free, recommended) or OPENAI_API_KEY in your environment variables."
        )

    texts = [chunk["content"] for chunk in chunks]

    if use_hf:
        logger.info(f"Generating embeddings via Hugging Face Inference API: {model}")
        hf_model = model if "/" in model else "BAAI/bge-small-en-v1.5"
        all_vectors = await _embed_via_huggingface(texts, hf_model)
        provider = "huggingface"
        active_model = hf_model
    else:
        logger.info(f"Generating embeddings via OpenAI: {model}")
        openai_model = model if "/" not in model else "text-embedding-3-small"
        all_vectors = await _embed_via_openai(texts, openai_model)
        provider = "openai"
        active_model = openai_model

    elapsed = time.time() - start_time
    latency = elapsed / len(chunks) if chunks else 0.0
    dimension = len(all_vectors[0]) if all_vectors else getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384)

    processed_embeddings = []
    for idx, chunk in enumerate(chunks):
        vector = all_vectors[idx]
        processed_embeddings.append({
            "chunk_id": chunk.get("id"),
            "chunk_number": chunk.get("chunk_number"),
            "embedding_model": active_model,
            "embedding_version": "1.0.0",
            "dimension": dimension,
            "status": "completed",
            "generated_time": datetime.now(timezone.utc),
            "provider": provider,
            "latency": round(latency, 4),
            "vector": vector,
            "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
        })

    logger.info(f"Embeddings generated: {len(processed_embeddings)} chunks via {provider} ({dimension}d) in {elapsed:.2f}s")
    return processed_embeddings


async def generate_query_embedding(
    query: str,
    model: str = None
) -> List[float]:
    """
    Generate a single query embedding for retrieval.
    Provider resolution same as generate_chunk_embeddings.
    """
    if model is None:
        model = settings.KNOWLEDGE_EMBEDDING_MODEL

    use_hf = bool(settings.HF_API_TOKEN and settings.HF_API_TOKEN.strip())
    use_openai = bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY not in ("", "REPLACE_WITH_REAL_VALUE"))

    if not use_hf and not use_openai:
        raise ValueError(
            "No embedding provider configured. "
            "Set HF_API_TOKEN (free, recommended) or OPENAI_API_KEY in your environment variables."
        )

    if use_hf:
        hf_model = model if "/" in model else "BAAI/bge-small-en-v1.5"
        vectors = await _embed_via_huggingface([query], hf_model)
        return vectors[0]
    else:
        openai_model = model if "/" not in model else "text-embedding-3-small"
        vectors = await _embed_via_openai([query], openai_model)
        return vectors[0]


def generate_mock_embedding(text: str, dimension: int = None) -> List[float]:
    """
    Generates a deterministic mock embedding vector for testing.
    Dimension matches KNOWLEDGE_EMBEDDING_DIMENSION (default 384).
    """
    import math
    if dimension is None:
        dimension = getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384)
    res = []
    for i in range(dimension):
        h = hashlib.md5(f"{text}:{i}".encode("utf-8")).hexdigest()
        val = int(h[:8], 16) / 4294967295.0
        res.append(val)
    norm = math.sqrt(sum(x * x for x in res))
    if norm > 0.0:
        res = [x / norm for x in res]
    return res
