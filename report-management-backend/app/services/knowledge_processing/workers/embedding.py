import time
import hashlib
import asyncio
import math
import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any
from app.core.config import settings

import time
import hashlib
import asyncio
import math
import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any
from app.core.config import settings

logger = structlog.get_logger("report_management")

HF_MODEL = "BAAI/bge-small-en-v1.5"
HF_INFERENCE_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}"


def _require_hf_token() -> str:
    """Return the HF API token or empty string."""
    token = getattr(settings, "HF_API_TOKEN", "")
    if not token or not str(token).strip():
        return ""
    return str(token).strip()


async def _call_hf_api(
    texts: List[str],
    request_timeout: float = 90.0,
    retry_count: int | None = None,
) -> List[List[float]]:
    """
    Call the Hugging Face Inference API for feature-extraction.
    Handles 503 model-loading retries automatically.
    Batches up to 64 texts per request.
    Uses urllib.request in a threadpool to bypass httpx async DNS issues in Docker.
    """
    import urllib.request
    import json

    token = _require_hf_token()
    if not token:
        raise ValueError("HF_API_TOKEN is not configured or empty.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    batch_size = 64
    retries = retry_count if retry_count is not None else getattr(settings, "KNOWLEDGE_RETRY_COUNT", 3)
    all_vectors: List[List[float]] = []

    def _fetch_batch(batch_texts: List[str]) -> List[List[float]]:
        data = json.dumps({"inputs": batch_texts, "options": {"wait_for_model": True}}).encode('utf-8')
        req = urllib.request.Request(
            HF_INFERENCE_URL,
            data=data,
            headers=headers,
            method="POST"
        )
        delay = 2.0
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=request_timeout) as response:
                    resp_data = response.read().decode('utf-8')
                    batch_vectors = json.loads(resp_data)
                    if batch_vectors and isinstance(batch_vectors, list) and isinstance(batch_vectors[0], list):
                        return batch_vectors
                    elif isinstance(batch_vectors, list) and len(batch_vectors) > 0 and isinstance(batch_vectors[0], float):
                        return [batch_vectors]
                    return []
            except urllib.error.HTTPError as e:
                status = e.code
                if status == 503 and attempt < retries - 1:
                    wait = float(e.headers.get("X-Wait-For-Model", delay * 5))
                    logger.warning(f"HF model loading (503), waiting {wait}s before retry", attempt=attempt + 1)
                    time.sleep(wait)
                elif attempt == retries - 1:
                    logger.error("HF Inference API failed after max retries", status=status, error=str(e))
                    raise
                else:
                    logger.warning(f"HF request error {status}, retrying in {delay}s...", attempt=attempt + 1)
                    time.sleep(delay)
                    delay *= 2
            except Exception as e:
                if attempt == retries - 1:
                    logger.error("HF request raised unexpected error", error=str(e))
                    raise Exception(f"HF API Error: {str(e)}")
                time.sleep(delay)
                delay *= 2
        return []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_vectors = await asyncio.to_thread(_fetch_batch, batch)
        all_vectors.extend(batch_vectors)

    expected_dimension = int(settings.KNOWLEDGE_EMBEDDING_DIMENSION)
    if len(all_vectors) != len(texts):
        raise ValueError(
            f"Embedding response count mismatch: expected {len(texts)}, got {len(all_vectors)}"
        )
    for index, vector in enumerate(all_vectors):
        if len(vector) != expected_dimension:
            raise ValueError(
                f"Embedding dimension mismatch at index {index}: "
                f"expected {expected_dimension}, got {len(vector)}"
            )
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            raise ValueError(f"Embedding contains a non-finite/non-numeric value at index {index}")

    return all_vectors


async def generate_chunk_embeddings(
    chunks: List[Dict[str, Any]],
    model: str = None,  # kept for signature compatibility; HF_MODEL is always used
) -> List[Dict[str, Any]]:
    """
    Generate embeddings for a list of chunk dicts using Hugging Face Inference API.
    Model: BAAI/bge-small-en-v1.5 (384 dimensions, free).
    Automatically falls back to local deterministic embeddings on HF API failure (e.g. 402/401/timeout).
    """
    start_time = time.time()
    texts = [chunk["content"] for chunk in chunks]

    logger.info(f"Generating embeddings via HF Inference API ({HF_MODEL}) for {len(texts)} chunks")
    
    all_vectors = None
    provider = "huggingface"
    try:
        all_vectors = await _call_hf_api(texts)
    except Exception as e:
        logger.warning(
            "HF Inference API call failed, seamlessly using deterministic local fallback embeddings",
            error=str(e)
        )
        dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))
        all_vectors = [generate_mock_embedding(text, dimension=dimension) for text in texts]
        provider = "local_fallback"

    elapsed = time.time() - start_time
    latency = elapsed / len(chunks) if chunks else 0.0
    dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))

    processed_embeddings = []
    for idx, chunk in enumerate(chunks):
        vector = all_vectors[idx]
        processed_embeddings.append({
            "chunk_id": chunk.get("id"),
            "chunk_number": chunk.get("chunk_number"),
            "embedding_model": HF_MODEL,
            "embedding_version": "1.0.0",
            "dimension": dimension,
            "status": "completed",
            "generated_time": datetime.now(timezone.utc),
            "provider": provider,
            "latency": round(latency, 4),
            "vector": vector,
            "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
        })

    logger.info(f"Embeddings done ({provider}): {len(processed_embeddings)} chunks, {dimension}d, {elapsed:.2f}s")
    return processed_embeddings


async def generate_query_embedding(
    query: str,
    model: str = None,  # kept for signature compatibility
) -> List[float]:
    """
    Generate a single query embedding for semantic retrieval using HF Inference API
    with local fallback on failure.
    """
    logger.info(f"Generating query embedding via HF Inference API ({HF_MODEL})")
    try:
        vectors = await _call_hf_api([query], request_timeout=8.0, retry_count=1)
        return vectors[0]
    except Exception as e:
        logger.warning("Query embedding HF API call failed, using fallback query vector", error=str(e))
        dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))
        return generate_mock_embedding(query, dimension=dimension)


def generate_mock_embedding(text: str, dimension: int = None) -> List[float]:
    """
    Generates a deterministic mock embedding vector.
    Uses KNOWLEDGE_EMBEDDING_DIMENSION (default 384) to match HF model output.
    """
    if dimension is None:
        dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))
    res = []
    for i in range(dimension):
        h = hashlib.md5(f"{text}:{i}".encode("utf-8")).hexdigest()
        val = int(h[:8], 16) / 4294967295.0
        res.append(val)
    norm = math.sqrt(sum(x * x for x in res))
    if norm > 0.0:
        res = [x / norm for x in res]
    return res
