import time
import hashlib
import random
from datetime import datetime, timezone
from typing import List, Dict, Any

def generate_mock_embedding(text: str, dimension: int = 1536) -> List[float]:
    """
    Generates a deterministic pseudo-random unit vector based on the text hash.
    Ensures identical texts get identical embeddings, and similarity score calculations work.
    """
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rng = random.Random(int(h, 16))
    
    # Generate floats between -1 and 1
    vec = [rng.uniform(-1, 1) for _ in range(dimension)]
    
    # Normalize to unit vector
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    else:
        vec = [0.0] * dimension
        vec[0] = 1.0
        
    return vec

async def generate_chunk_embeddings(
    chunks: List[Dict[str, Any]], 
    model: str = "text-embedding-3-small"
) -> List[Dict[str, Any]]:
    start_time = time.time()
    processed_embeddings = []
    
    for chunk in chunks:
        text = chunk["content"]
        
        # Determine dimensions (OpenAI text-embedding-3-small default is 1536)
        dimension = 1536
        if "large" in model:
            dimension = 3072
            
        vector = generate_mock_embedding(text, dimension=dimension)
        
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
            "provider": "mock_openai",
            "latency": round(latency, 4),
            "vector": vector,
            "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
        })
        
    return processed_embeddings
