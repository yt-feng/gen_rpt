import re

def normalize_text(text: str) -> str:
    if not text:
        return ""
        
    # Replace carriage returns and vertical tabs
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    
    # Strip bad control characters (keeping standard ascii/unicode spacing and tabs)
    # Control chars: 0x00 to 0x1f except 0x09 (tab), 0x0a (newline)
    normalized = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", normalized)
    
    # Clean up multiple consecutive empty lines to maximum 2 empty lines
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    
    # Trim trailing space on each line
    lines = [line.rstrip() for line in normalized.split("\n")]
    
    # Reassemble
    normalized = "\n".join(lines)
    
    return normalized
