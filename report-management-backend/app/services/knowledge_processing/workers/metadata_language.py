import re
from datetime import datetime, timezone
from typing import Dict, Any

# Stopword sets for language detection
LANG_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "is", "that", "it", "he", "was", "for", "on", "as", "with", "his", "they", "at"},
    "fr": {"le", "la", "les", "et", "de", "un", "une", "des", "en", "que", "est", "dans", "pour", "qui", "dans", "par"},
    "de": {"der", "die", "das", "und", "in", "ist", "zu", "den", "von", "mit", "sich", "auf", "für", "ein", "eine"},
    "es": {"el", "la", "los", "y", "en", "un", "una", "de", "que", "es", "con", "para", "por", "del", "lo", "como"},
    "ar": {"من", "في", "على", "و", "أن", "هذا", "هذه", "إلى", "مع", "أو", "لا", "كان", "كل", "هو", "هي"}
}

def detect_language(text: str) -> Dict[str, Any]:
    # Check for Chinese / CJK characters first
    cjk_matches = re.findall(r"[\u4e00-\u9fff]", text or "")
    if len(cjk_matches) > 5:
        return {
            "primary_language": "zh",
            "secondary_languages": [],
            "confidence_score": 0.95,
            "language_distribution": {"zh": 1.0}
        }
    # Clean and split text into lowercase words
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return {
            "primary_language": "en",
            "secondary_languages": [],
            "confidence_score": 1.0,
            "language_distribution": {"en": 1.0}
        }
        
    counts = {lang: 0 for lang in LANG_STOPWORDS}
    for word in words:
        for lang, stopwords in LANG_STOPWORDS.items():
            if word in stopwords:
                counts[lang] += 1
                
    total_matches = sum(counts.values())
    if total_matches == 0:
        return {
            "primary_language": "en",
            "secondary_languages": [],
            "confidence_score": 0.5,
            "language_distribution": {"en": 1.0}
        }
        
    distribution = {lang: round(count / total_matches, 3) for lang, count in counts.items() if count > 0}
    sorted_langs = sorted(distribution.items(), key=lambda x: x[1], reverse=True)
    
    primary_lang = sorted_langs[0][0]
    confidence = sorted_langs[0][1]
    
    secondary = [lang for lang, prob in sorted_langs[1:] if prob > 0.15]
    
    return {
        "primary_language": primary_lang,
        "secondary_languages": secondary,
        "confidence_score": confidence,
        "language_distribution": distribution
    }

def extract_metadata_package(text: str, filename: str, mime_type: str, extension: str, file_size: int, extraction_metadata: Dict[str, Any]) -> Dict[str, Any]:
    lang_info = detect_language(text)
    
    char_count = len(text)
    word_count = len(text.split())
    token_estimate = int(char_count / 4)
    
    # Try to guess a clean title from the text (e.g. the first line or markdown heading)
    title = filename
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if lines:
        first_line = lines[0]
        # Clean markdown prefix
        first_line = re.sub(r"^#{1,6}\s+", "", first_line)
        if len(first_line) < 100:
            title = first_line
            
    now_str = datetime.now(timezone.utc).isoformat()
    
    metadata = {
        "title": title,
        "author": "System Ingest",
        "publisher": "Enterprise Knowledge System",
        "creation_date": now_str,
        "modification_date": now_str,
        "language": lang_info["primary_language"],
        "secondary_languages": lang_info["secondary_languages"],
        "language_distribution": lang_info["language_distribution"],
        "language_confidence": lang_info["confidence_score"],
        "keywords": [],
        "page_count": extraction_metadata.get("page_count", 1),
        "document_type": mime_type,
        "encoding": "utf-8",
        "character_count": char_count,
        "word_count": word_count,
        "token_estimate": token_estimate,
        "file_statistics": {
            "size_bytes": file_size,
            "extension": extension.lstrip("."),
            "mime_type": mime_type
        }
    }
    return metadata
