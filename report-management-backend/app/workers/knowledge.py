from abc import ABC, abstractmethod
from typing import Dict, Any

class DocumentProcessor(ABC):
    """Interface for extracting text and metadata from files."""
    @abstractmethod
    async def process(self, file_bytes: bytes, file_name: str) -> Dict[str, Any]:
        pass

class EmbeddingProcessor(ABC):
    """Interface for generating vector embeddings from text chunks."""
    @abstractmethod
    async def generate_embedding(self, text: str) -> list[float]:
        pass

class ChunkProcessor(ABC):
    """Interface for segmenting document text into semantic chunks."""
    @abstractmethod
    def chunk_text(self, text: str, options: Dict[str, Any]) -> list[Dict[str, Any]]:
        pass

class ValidationProcessor(ABC):
    """Interface for verifying chunk evidence completeness and trustworthiness."""
    @abstractmethod
    async def validate_chunks(self, chunks: list[Dict[str, Any]]) -> Dict[str, Any]:
        pass

class OCRProcessor(ABC):
    """Interface for running Optical Character Recognition on scanned files."""
    @abstractmethod
    async def perform_ocr(self, image_or_pdf_bytes: bytes) -> str:
        pass

class MetadataProcessor(ABC):
    """Interface for extracting structured document metadata."""
    @abstractmethod
    async def extract_metadata(self, text: str) -> Dict[str, Any]:
        pass


# Concrete stubs for worker endpoints
class StubDocumentProcessor(DocumentProcessor):
    async def process(self, file_bytes: bytes, file_name: str) -> Dict[str, Any]:
        return {"text": "stub text", "metadata": {}}

class StubEmbeddingProcessor(EmbeddingProcessor):
    async def generate_embedding(self, text: str) -> list[float]:
        return [0.0] * 1536

class StubChunkProcessor(ChunkProcessor):
    def chunk_text(self, text: str, options: Dict[str, Any]) -> list[Dict[str, Any]]:
        return [{"content": text, "chunk_index": 0}]

class StubValidationProcessor(ValidationProcessor):
    async def validate_chunks(self, chunks: list[Dict[str, Any]]) -> Dict[str, Any]:
        return {"is_valid": True, "results": []}

class StubOCRProcessor(OCRProcessor):
    async def perform_ocr(self, image_or_pdf_bytes: bytes) -> str:
        return "ocr text stub"

class StubMetadataProcessor(MetadataProcessor):
    async def extract_metadata(self, text: str) -> Dict[str, Any]:
        return {"author": "Unknown", "tags": []}
