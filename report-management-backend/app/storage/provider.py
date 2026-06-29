import boto3
from typing import Optional, Union, BinaryIO
from abc import ABC, abstractmethod
from app.core.config import settings
from app.logging.logger import logger

class StorageProvider(ABC):
    @abstractmethod
    def upload(self, file_data: Union[bytes, BinaryIO], path: str) -> bool:
        pass
        
    @abstractmethod
    def download(self, path: str) -> Optional[bytes]:
        pass
        
    @abstractmethod
    def delete(self, path: str) -> bool:
        pass
        
    @abstractmethod
    def exists(self, path: str) -> bool:
        pass
        
    @abstractmethod
    def get_signed_url(self, path: str, expiration_sec: int = 3600) -> str:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass


class CloudflareR2Provider(StorageProvider):
    def __init__(self):
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto"
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload(self, file_data: Union[bytes, BinaryIO], path: str) -> bool:
        return False # Not implemented yet

    def download(self, path: str) -> Optional[bytes]:
        return None # Not implemented yet

    def delete(self, path: str) -> bool:
        return False # Not implemented yet

    def exists(self, path: str) -> bool:
        return False # Not implemented yet

    def get_signed_url(self, path: str, expiration_sec: int = 3600) -> str:
        return "Not Implemented"

    def health_check(self) -> bool:
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
            return True
        except Exception as e:
            logger.error(f"R2 Health check failed: {e}")
            return False
