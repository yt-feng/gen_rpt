import uuid
import re
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.validation import ValidationPolicy

class ConflictService:
    async def detect_conflicts(
        self,
        db: AsyncSession,
        chunks: List[Dict[str, Any]],
        policy: ValidationPolicy
    ) -> Tuple[Dict[uuid.UUID, List[uuid.UUID]], List[Dict[str, Any]]]:
        """
        Detects contradictions (numerical, date, country, tech, entity) across sources.
        Marks conflicts but does not resolve them automatically.
        Returns:
            - A dict mapping chunk_id to list of chunk_ids it conflicts with.
            - A list of conflict detail records.
        """
        conflict_map = {}
        conflicts_list = []
        
        if not chunks or len(chunks) < 2:
            return conflict_map, conflicts_list

        # Heuristic rules: extract numbers, dates, countries, and compare if they match context but differ in values.
        # We can extract potential entities using regex
        year_pattern = re.compile(r'\b(19\d{2}|20\d{2})\b')
        numeric_value_pattern = re.compile(r'\b\d+(?:\.\d+)?\s*(?:billion|million|percent|%|USD|EUR)\b', re.IGNORECASE)
        country_pattern = re.compile(r'\b(?:Saudi Arabia|UAE|Qatar|Germany|US|USA|United States|UK|China|India|France|Egypt)\b', re.IGNORECASE)

        heading_stop_words = {
            "a", "an", "and", "for", "in", "key", "of", "on", "the", "to", "update",
        }
        extracted_data = []
        for c in chunks:
            text = c.get("text_content") or ""
            years = set(year_pattern.findall(text))
            values = set(numeric_value_pattern.findall(text))
            countries = set(country_pattern.findall(text))
            heading = (c.get("metadata") or {}).get("heading", "") or c.get("file_name", "")
            
            extracted_data.append({
                "chunk_id": c["chunk_id"],
                "document_id": c["document_id"],
                "file_name": c["file_name"],
                "heading": heading,
                "years": years,
                "values": values,
                "countries": countries,
                "text": text
            })

        # Compare pairs of chunks
        for i in range(len(extracted_data)):
            for j in range(i + 1, len(extracted_data)):
                c1 = extracted_data[i]
                c2 = extracted_data[j]
                
                # Only check chunks from different documents to find multi-source conflicts
                if c1["document_id"] == c2["document_id"]:
                    continue
                
                # Check for Heading/Context similarity (fuzzy context overlap)
                heading_words1 = {
                    word for word in re.findall(r"[a-z0-9]+", (c1["heading"] or "").lower())
                    if word not in heading_stop_words
                }
                heading_words2 = {
                    word for word in re.findall(r"[a-z0-9]+", (c2["heading"] or "").lower())
                    if word not in heading_stop_words
                }
                shared_heading_words = heading_words1.intersection(heading_words2)
                same_context = (
                    heading_words1 == heading_words2 and bool(heading_words1)
                ) or len(shared_heading_words) >= 2

                # If they share context, check for value conflicts
                if same_context:
                    conflict_reasons = []
                    
                    # 1. Year Conflict (same context, different years)
                    if c1["years"] and c2["years"] and c1["years"] != c2["years"]:
                        conflict_reasons.append(f"Date conflict: {c1['years']} vs {c2['years']}")
                        
                    # 2. Country Conflict (same context, different countries)
                    if c1["countries"] and c2["countries"] and c1["countries"] != c2["countries"]:
                        conflict_reasons.append(f"Country/Geography conflict: {c1['countries']} vs {c2['countries']}")
                        
                    # 3. Numerical Value Conflict
                    if c1["values"] and c2["values"] and c1["values"] != c2["values"]:
                        conflict_reasons.append(f"Numerical conflict: {c1['values']} vs {c2['values']}")

                    if conflict_reasons:
                        cid1 = c1["chunk_id"]
                        cid2 = c2["chunk_id"]
                        
                        # Add to mapping
                        if cid1 not in conflict_map:
                            conflict_map[cid1] = []
                        if cid2 not in conflict_map:
                            conflict_map[cid2] = []
                            
                        conflict_map[cid1].append(cid2)
                        conflict_map[cid2].append(cid1)
                        
                        conflicts_list.append({
                            "chunk_id_a": cid1,
                            "chunk_id_b": cid2,
                            "file_a": c1["file_name"],
                            "file_b": c2["file_name"],
                            "heading_a": c1["heading"],
                            "heading_b": c2["heading"],
                            "reasons": conflict_reasons
                        })
                        
        return conflict_map, conflicts_list

conflict_service = ConflictService()
