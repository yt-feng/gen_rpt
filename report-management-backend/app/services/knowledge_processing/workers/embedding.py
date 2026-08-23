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


async def _call_ollama_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Call local Ollama embeddings API for a batch of texts.
    Truncates/pads output vectors to KNOWLEDGE_EMBEDDING_DIMENSION (384)
    and L2-normalizes the result.
    """
    import urllib.request
    import json
    import math

    dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))
    url = getattr(settings, "OLLAMA_EMBEDDING_URL", "http://localhost:11434/api/embeddings")
    model = getattr(settings, "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

    all_vectors = []
    for text in texts:
        data = json.dumps({"model": model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        def _post():
            with urllib.request.urlopen(req, timeout=15.0) as response:
                resp_data = json.loads(response.read().decode('utf-8'))
                return resp_data.get("embedding")

        logger.info(f"Querying local Ollama embedding endpoint using model '{model}'")
        vector = await asyncio.to_thread(_post)
        if not vector or not isinstance(vector, list):
            raise ValueError("Invalid response from Ollama embeddings endpoint")

        if len(vector) > dimension:
            vector = vector[:dimension]
        elif len(vector) < dimension:
            vector = vector + [0.0] * (dimension - len(vector))

        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]
        all_vectors.append(vector)

    return all_vectors


async def _call_openai_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Call OpenAI / OpenRouter embeddings API.
    Uses settings.OPENAI_EMBEDDING_MODEL and configures the dimensions parameter
    to match KNOWLEDGE_EMBEDDING_DIMENSION (384) directly from the API.
    """
    import urllib.request
    import json
    import os

    api_key = getattr(settings, "OPENAI_EMBEDDING_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_EMBEDDING_API_KEY is not configured or empty.")

    url = getattr(settings, "OPENAI_EMBEDDING_URL", "https://api.openai.com/v1/embeddings")
    model = getattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    batch_size = 64
    all_vectors = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        payload = {
            "input": batch,
            "model": model,
            "dimensions": dimension
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST"
        )

        def _post():
            with urllib.request.urlopen(req, timeout=30.0) as response:
                resp_data = json.loads(response.read().decode('utf-8'))
                return [item["embedding"] for item in resp_data.get("data", [])]

        logger.info(f"Querying OpenAI embedding endpoint for batch of {len(batch)} items using model '{model}'")
        batch_vectors = await asyncio.to_thread(_post)
        if len(batch_vectors) != len(batch):
            raise ValueError("Mismatched vector counts returned from OpenAI embeddings API")
        all_vectors.extend(batch_vectors)

    return all_vectors


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
                if status in (401, 402):
                    logger.error("HF Inference API status error, fast failing to local fallback", status=status, error=str(e))
                    raise
                elif status == 503 and attempt < retries - 1:
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


async def _get_embeddings_with_fallback(texts: List[str]) -> tuple[List[List[float]], str]:
    """
    Attempt to fetch embeddings from Hugging Face first.
    If HF fails or returns a 402/rate-limit error, fall back dynamically to:
      1. Ollama (if EMBEDDING_FALLBACK_PROVIDER is 'ollama')
      2. OpenAI (if EMBEDDING_FALLBACK_PROVIDER is 'openai')
      3. Deterministic local mock vector generator as final fallback.
    """
    try:
        vectors = await _call_hf_api(texts)
        return vectors, "huggingface"
    except Exception as hf_err:
        fallback_provider = getattr(settings, "EMBEDDING_FALLBACK_PROVIDER", "ollama")
        logger.warning(f"Hugging Face embedding generation failed. Initiating fallback cascade using provider '{fallback_provider}'...", error=str(hf_err))

    if fallback_provider == "ollama":
        try:
            model = getattr(settings, "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
            logger.info(f"Attempting Ollama embedding fallback using model '{model}'...")
            vectors = await _call_ollama_embeddings(texts)
            return vectors, "ollama_fallback"
        except Exception as ollama_err:
            logger.error("Ollama fallback failed.", error=str(ollama_err))
    elif fallback_provider == "openai":
        try:
            model = getattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
            logger.info(f"Attempting OpenAI embedding fallback using model '{model}'...")
            vectors = await _call_openai_embeddings(texts)
            return vectors, "openai_fallback"
        except Exception as openai_err:
            logger.error("OpenAI fallback failed.", error=str(openai_err))

    logger.warning("All primary/secondary embedding providers failed. Falling back to local deterministic mock vector generator.")
    dimension = int(getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384))
    vectors = [generate_mock_embedding(text, dimension=dimension) for text in texts]
    return vectors, "local_fallback"


async def generate_chunk_embeddings(
    chunks: List[Dict[str, Any]],
    model: str = None,  # kept for signature compatibility; HF_MODEL is always used
) -> List[Dict[str, Any]]:
    """
    Generate embeddings for a list of chunk dicts using Hugging Face Inference API with fallback cascade.
    Model: BAAI/bge-small-en-v1.5 (384 dimensions, free).
    """
    start_time = time.time()
    texts = [chunk["content"] for chunk in chunks]

    logger.info(f"Generating embeddings via HF Inference API ({HF_MODEL}) for {len(texts)} chunks")

    all_vectors = None
    provider = "huggingface"
    try:
        all_vectors, provider = await _get_embeddings_with_fallback(texts)
    except Exception as e:
        logger.warning(
            "Embedding fallback routing encountered unhandled error, using deterministic local mock",
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
    Generate a single query embedding for semantic retrieval with dynamic fallback logic.
    """
    logger.info(f"Generating query embedding via API endpoint")
    try:
        vectors, provider = await _get_embeddings_with_fallback([query])
        return vectors[0]
    except Exception as e:
        logger.warning("Query embedding fallback cascade failed, using local mock query vector", error=str(e))
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
