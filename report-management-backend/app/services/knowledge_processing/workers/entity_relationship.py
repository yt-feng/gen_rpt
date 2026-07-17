import re
from typing import List, Dict, Any, Tuple

# Predefined lexicons for simple entity extraction
LEXICON = {
    "organization": {"Google", "Microsoft", "Apple", "DeepMind", "Meta", "OpenAI", "Cloudflare", "Supabase", "Amazon", "IBM"},
    "country": {"United States", "France", "Germany", "Japan", "Egypt", "UAE", "Saudi Arabia", "United Kingdom", "Canada"},
    "city": {"Paris", "London", "Tokyo", "Cairo", "Dubai", "Riyadh", "New York", "San Francisco"},
    "technology": {"RAG", "SQLAlchemy", "FastAPI", "Python", "Supabase", "pgvector", "PostgreSQL", "Alembic", "SQLite", "Docker"},
    "product": {"NotebookLM", "Gemini", "ChatGPT", "Claude", "R2", "S3", "Office"},
    "law_policy": {"GDPR", "HIPAA", "CCPA", "Privacy Policy", "Terms of Service", "Compliance Act"}
}

def extract_entities_and_relationships(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    entities = []
    relationships = []
    
    found_entities_map = {} # name -> type
    
    # 1. Lexicon extraction
    # Clean words matching lexicons
    words = set(re.findall(r"\b[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)?\b", text))
    
    for word in words:
        # Check against lexicons
        for ent_type, names in LEXICON.items():
            # Direct match or case-insensitive match
            for name in names:
                if word.lower() == name.lower():
                    found_entities_map[name] = ent_type
                    
    # Also extract capitalized nouns as Person or Organization if not already matched
    candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", text)
    for cand in candidates:
        if cand not in found_entities_map and cand not in {"The", "A", "An", "In", "On", "At", "For", "To", "And", "Or"}:
            # Basic heuristic: if it contains "CEO" or "Manager" or "Dr." nearby, it's a person, otherwise tag as organizational candidate or person
            found_entities_map[cand] = "person"
            
    # Format entities list
    for ent_name, ent_type in found_entities_map.items():
        # Count frequency
        freq = len(re.findall(r"\b" + re.escape(ent_name) + r"\b", text, re.IGNORECASE))
        if freq > 0:
            entities.append({
                "name": ent_name,
                "type": ent_type,
                "frequency": freq,
                "confidence": 0.95 if ent_name in LEXICON.get(ent_type, {}) else 0.75
            })
            
    # 2. Relationship extraction
    # Check co-occurrences within sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    relationship_set = set()
    
    for sentence in sentences:
        sentence_entities = []
        for ent_name in found_entities_map:
            if re.search(r"\b" + re.escape(ent_name) + r"\b", sentence, re.IGNORECASE):
                sentence_entities.append(ent_name)
                
        # Link entity pairs in same sentence
        for i in range(len(sentence_entities)):
            for j in range(i + 1, len(sentence_entities)):
                ent1 = sentence_entities[i]
                ent2 = sentence_entities[j]
                
                # Standardize pair order to avoid duplicate relations in opposite direction
                pair = tuple(sorted([ent1, ent2]))
                if pair not in relationship_set:
                    relationship_set.add(pair)
                    
                    # Deduce basic relation types
                    t1 = found_entities_map[ent1]
                    t2 = found_entities_map[ent2]
                    
                    rel_type = "related_to"
                    if t1 == "person" and t2 == "organization":
                        rel_type = "member_of"
                    elif t1 == "organization" and t2 == "product":
                        rel_type = "creates"
                    elif t1 == "product" and t2 == "technology":
                        rel_type = "uses"
                    elif t1 == "technology" and t2 == "technology":
                        rel_type = "depends_on"
                        
                    relationships.append({
                        "source": ent1,
                        "target": ent2,
                        "type": rel_type,
                        "sentence": sentence.strip()[:200]
                    })
                    
    return entities, relationships
