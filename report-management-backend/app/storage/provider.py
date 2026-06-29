import boto3
from typing import Optional, Union, BinaryIO
from abc import ABC, abstractmethod
from botocore.exceptions import ClientError
from anyio import to_thread
from app.core.config import settings
from app.logging.logger import logger

class StorageProvider(ABC):
    @abstractmethod
    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        pass
        
    @abstractmethod
    async def download(self, path: str) -> Optional[bytes]:
        pass
        
    @abstractmethod
    async def delete(self, path: str) -> bool:
        pass
        
    @abstractmethod
    async def exists(self, path: str) -> bool:
        pass
        
    @abstractmethod
    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        pass

    @abstractmethod
    async def health_check(self) -> dict:
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
        self.bucket = settings.R2_BUCKET

    def _upload_sync(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str) -> bool:
        try:
            if isinstance(file_data, bytes):
                self.s3_client.put_object(Bucket=self.bucket, Key=path, Body=file_data, ContentType=content_type)
            else:
                self.s3_client.upload_fileobj(file_data, self.bucket, path, ExtraArgs={'ContentType': content_type})
            return True
        except ClientError as e:
            logger.error(f"R2 Upload failed for {path}: {e}")
            return False

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        return await to_thread.run_sync(self._upload_sync, file_data, path, content_type)

    def _download_sync(self, path: str) -> Optional[bytes]:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=path)
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"R2 Download failed for {path}: {e}")
            return None

    async def download(self, path: str) -> Optional[bytes]:
        return await to_thread.run_sync(self._download_sync, path)

    def _delete_sync(self, path: str) -> bool:
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except ClientError as e:
            logger.error(f"R2 Delete failed for {path}: {e}")
            return False

    async def delete(self, path: str) -> bool:
        return await to_thread.run_sync(self._delete_sync, path)

    def _exists_sync(self, path: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return True
        except ClientError as e:
            # 404 is expected if not found
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"R2 Exists check failed for {path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        return await to_thread.run_sync(self._exists_sync, path)

    def _get_signed_url_sync(self, path: str, expiration_sec: int, method: str) -> str:
        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod=method,
                Params={'Bucket': self.bucket, 'Key': path},
                ExpiresIn=expiration_sec
            )
            return url
        except ClientError as e:
            logger.error(f"R2 Signed URL generation failed for {path}: {e}")
            return ""

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        return await to_thread.run_sync(self._get_signed_url_sync, path, expiration_sec, method)

    def _health_check_sync(self) -> dict:
        import time
        start = time.time()
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
            latency = round((time.time() - start) * 1000, 2)
            return {"status": "healthy", "latency_ms": latency}
        except Exception as e:
            logger.error(f"R2 Health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def health_check(self) -> dict:
        return await to_thread.run_sync(self._health_check_sync)

storage_provider = CloudflareR2Provider()
