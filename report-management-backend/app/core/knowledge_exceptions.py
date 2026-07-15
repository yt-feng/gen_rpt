from fastapi import HTTPException, status

class KnowledgeException(HTTPException):
    """Base exception for all Knowledge Intelligence workflows."""
    def __init__(
        self,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail: str = "A knowledge intelligence error occurred"
    ):
        super().__init__(status_code=status_code, detail=detail)

class CollectionNotFoundException(KnowledgeException):
    """Raised when a requested knowledge collection cannot be found."""
    def __init__(self, collection_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge Collection '{collection_id}' not found"
        )

class DocumentProcessingException(KnowledgeException):
    """Raised when document extraction, parsing, or chunking fails."""
    def __init__(self, document_id: str, detail: str = "Processing failed"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Document '{document_id}' processing failed: {detail}"
        )

class RetrievalFailedException(KnowledgeException):
    """Raised when semantic retrieval or hybrid search query fails."""
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Knowledge retrieval failed: {detail}"
        )

class ValidationFailedException(KnowledgeException):
    """Raised when retrieved knowledge fails validation filters."""
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge validation failed: {detail}"
        )
