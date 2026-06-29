import hashlib
from typing import Union, BinaryIO, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.provider import storage_provider
from app.repositories.document_file import document_file_repo
from app.schemas.document import DocumentFileCreate
from app.models.document import DocumentFile
from app.logging.logger import logger

class StorageService:
    @staticmethod
    def generate_path(document_id: UUID, version_id: UUID, filename: str, file_type: str) -> str:
        """
        Generates deterministic, version-aware paths.
        e.g., reports/123/versions/456/pdf/report.pdf
        """
        return f"reports/{document_id}/versions/{version_id}/{file_type}/{filename}"

    @staticmethod
    def generate_asset_path(document_id: UUID, asset_id: UUID, filename: str) -> str:
        """
        Generates path for non-versioned assets (like images used across versions).
        """
        return f"reports/{document_id}/assets/{asset_id}/{filename}"

    @staticmethod
    def calculate_checksum(file_data: Union[bytes, BinaryIO]) -> str:
        hasher = hashlib.sha256()
        if isinstance(file_data, bytes):
            hasher.update(file_data)
        else:
            file_data.seek(0)
            while chunk := file_data.read(8192):
                hasher.update(chunk)
            file_data.seek(0)
        return hasher.hexdigest()

    @staticmethod
    def get_size(file_data: Union[bytes, BinaryIO]) -> int:
        if isinstance(file_data, bytes):
            return len(file_data)
        else:
            file_data.seek(0, 2)
            size = file_data.tell()
            file_data.seek(0)
            return size

    async def upload_document_file(
        self,
        db: AsyncSession,
        document_id: UUID,
        version_id: UUID,
        filename: str,
        file_type: str,
        content_type: str,
        file_data: Union[bytes, BinaryIO]
    ) -> Optional[DocumentFile]:
        """
        Uploads an object to R2 and creates the authoritative metadata record in Supabase.
        Rolls back the R2 upload if the database insert fails.
        """
        path = self.generate_path(document_id, version_id, filename, file_type)
        
        # Validation
        checksum = self.calculate_checksum(file_data)
        size = self.get_size(file_data)

        # 1. Upload to Object Storage
        success = await storage_provider.upload(file_data, path, content_type)
        if not success:
            logger.error(f"Failed to upload {path} to object storage.")
            return None

        # 2. Sync with Database
        file_in = DocumentFileCreate(
            version_id=version_id,
            file_type=file_type,
            storage_path=path,
            checksum=checksum,
            size=size
        )

        try:
            db_file = await document_file_repo.create(db=db, obj_in=file_in)
            return db_file
        except Exception as e:
            # Rollback storage if DB fails
            logger.error(f"Database insert failed for {path}, rolling back storage upload: {e}")
            await storage_provider.delete(path)
            raise e

    async def get_signed_url(self, db: AsyncSession, file_id: UUID, expiration_sec: int = 3600) -> Optional[str]:
        """
        Returns a signed URL after authorizing the file existence in DB.
        """
        db_file = await document_file_repo.get(db, file_id)
        if not db_file:
            return None
            
        return await storage_provider.get_signed_url(db_file.storage_path, expiration_sec)

    async def delete_document_file(self, db: AsyncSession, file_id: UUID) -> bool:
        """
        Deletes the object from R2 and removes the database record.
        """
        db_file = await document_file_repo.get(db, file_id)
        if not db_file:
            return False

        # Delete from R2 first
        success = await storage_provider.delete(db_file.storage_path)
        if not success:
            logger.error(f"Failed to delete {db_file.storage_path} from object storage.")
            return False
            
        # Delete from DB
        await document_file_repo.remove(db, id=file_id)
        return True

storage_service = StorageService()
