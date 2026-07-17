import re
import hashlib
from typing import List, Dict, Any

def chunk_document(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
    # Split document by double newlines to isolate paragraphs/sections
    paragraphs = text.split("\n\n")
    chunks = []
    
    current_chunk_parts = []
    current_length = 0
    chunk_number = 1
    
    current_heading = "Introduction"
    current_section = None
    
    # Process element-by-element (paragraphs)
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
            
        # Detect if paragraph is a markdown header
        header_match = re.match(r"^(#{1,6})\s+(.*)$", p)
        if header_match:
            current_heading = header_match.group(2).strip()
            current_section = current_heading
            
        p_len = len(p)
        
        # If this single paragraph exceeds the chunk size, we need to split it by sentences
        if p_len > chunk_size:
            # First flush existing chunk if any
            if current_chunk_parts:
                chunk_text = "\n\n".join(current_chunk_parts)
                chunks.append({
                    "chunk_number": chunk_number,
                    "content": chunk_text,
                    "heading": current_heading,
                    "section": current_section,
                    "token_count": int(len(chunk_text) / 4),
                    "character_count": len(chunk_text),
                    "hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                })
                chunk_number += 1
                current_chunk_parts = []
                current_length = 0
                
            # Split paragraph into sentences or smaller blocks
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                s_len = len(s)
                if current_length + s_len > chunk_size and current_chunk_parts:
                    chunk_text = " ".join(current_chunk_parts)
                    chunks.append({
                        "chunk_number": chunk_number,
                        "content": chunk_text,
                        "heading": current_heading,
                        "section": current_section,
                        "token_count": int(len(chunk_text) / 4),
                        "character_count": len(chunk_text),
                        "hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                    })
                    chunk_number += 1
                    
                    # Implement overlap by keeping last elements
                    overlap_size = 0
                    overlap_parts = []
                    for part in reversed(current_chunk_parts):
                        if overlap_size + len(part) < chunk_overlap:
                            overlap_parts.insert(0, part)
                            overlap_size += len(part)
                        else:
                            break
                    current_chunk_parts = overlap_parts
                    current_length = overlap_size
                    
                current_chunk_parts.append(s)
                current_length += s_len
        else:
            # Paragraph fits
            if current_length + p_len > chunk_size and current_chunk_parts:
                chunk_text = "\n\n".join(current_chunk_parts)
                chunks.append({
                    "chunk_number": chunk_number,
                    "content": chunk_text,
                    "heading": current_heading,
                    "section": current_section,
                    "token_count": int(len(chunk_text) / 4),
                    "character_count": len(chunk_text),
                    "hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                })
                chunk_number += 1
                
                # Implement overlap
                overlap_size = 0
                overlap_parts = []
                for part in reversed(current_chunk_parts):
                    if overlap_size + len(part) < chunk_overlap:
                        overlap_parts.insert(0, part)
                        overlap_size += len(part) + 2 # +2 for newline
                    else:
                        break
                current_chunk_parts = overlap_parts
                current_length = overlap_size
                
            current_chunk_parts.append(p)
            current_length += p_len + 2 # +2 for newline
            
    # Flush final chunk
    if current_chunk_parts:
        chunk_text = "\n\n".join(current_chunk_parts)
        chunks.append({
            "chunk_number": chunk_number,
            "content": chunk_text,
            "heading": current_heading,
            "section": current_section,
            "token_count": int(len(chunk_text) / 4),
            "character_count": len(chunk_text),
            "hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        })
        
    return chunks

def build_headings_outline(text: str) -> Dict[str, Any]:
    # Extract structural outline tree
    lines = text.split("\n")
    outline = []
    
    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            outline.append({
                "line_number": i + 1,
                "level": level,
                "title": title
            })
            
    return {"outline": outline}
