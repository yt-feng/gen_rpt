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
    """Return the HF API token or raise a clear error."""
    token = settings.HF_API_TOKEN
    if not token or not token.strip():
        raise ValueError(
            "HF_API_TOKEN is not configured. "
            "Get a free token at https://huggingface.co/settings/tokens "
            "and add it to your Render environment variables."
        )
    return token.strip()


async def _call_hf_api(texts: List[str]) -> List[List[float]]:
    """
    Call the Hugging Face Inference API for feature-extraction.
    Handles 503 model-loading retries automatically.
    Batches up to 64 texts per request.
    Uses urllib.request in a threadpool to bypass httpx async DNS issues in Docker.
    """
    import urllib.request
    import json

    token = _require_hf_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    batch_size = 64
    retries = getattr(settings, "KNOWLEDGE_RETRY_COUNT", 3)
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
                with urllib.request.urlopen(req, timeout=90.0) as response:
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
    Requires HF_API_TOKEN environment variable.
    """
    start_time = time.time()
    texts = [chunk["content"] for chunk in chunks]

    logger.info(f"Generating embeddings via HF Inference API ({HF_MODEL}) for {len(texts)} chunks")
    all_vectors = await _call_hf_api(texts)

    elapsed = time.time() - start_time
    latency = elapsed / len(chunks) if chunks else 0.0
    dimension = int(settings.KNOWLEDGE_EMBEDDING_DIMENSION)

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
            "provider": "huggingface",
            "latency": round(latency, 4),
            "vector": vector,
            "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
        })

    logger.info(f"Embeddings done: {len(processed_embeddings)} chunks, {dimension}d, {elapsed:.2f}s")
    return processed_embeddings


async def generate_query_embedding(
    query: str,
    model: str = None,  # kept for signature compatibility
) -> List[float]:
    """
    Generate a single query embedding for semantic retrieval using HF Inference API.
    Requires HF_API_TOKEN environment variable.
    """
    logger.info(f"Generating query embedding via HF Inference API ({HF_MODEL})")
    vectors = await _call_hf_api([query])
    return vectors[0]


def generate_mock_embedding(text: str, dimension: int = None) -> List[float]:
    """
    Generates a deterministic mock embedding vector for testing.
    Uses KNOWLEDGE_EMBEDDING_DIMENSION (default 384) to match HF model output.
    NOT used in production — only in unit tests.
    """
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
