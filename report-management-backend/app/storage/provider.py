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
        self.is_configured = bool(
            settings.R2_ACCOUNT_ID and
            settings.R2_ACCESS_KEY_ID and
            settings.R2_SECRET_ACCESS_KEY and
            settings.R2_BUCKET
        )
        if self.is_configured:
            from botocore.config import Config
            endpoint = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
                config=Config(signature_version="s3v4")
            )
        else:
            self.s3_client = None
            logger.warning("Cloudflare R2 is not configured — storage operations will be unavailable.")
        self.bucket = settings.R2_BUCKET

    def _upload_sync(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str) -> bool:
        if not self.is_configured:
            logger.warning("R2 upload skipped — not configured")
            return False
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

    async def upload_streaming(self, file_object: BinaryIO, path: str, content_type: str = "application/octet-stream") -> bool:
        return await to_thread.run_sync(self._upload_sync, file_object, path, content_type)


    def _download_sync(self, path: str) -> Optional[bytes]:
        if not self.is_configured:
            return None
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=path)
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"R2 Download failed for {path}: {e}")
            return None

    async def download(self, path: str) -> Optional[bytes]:
        return await to_thread.run_sync(self._download_sync, path)

    def _delete_sync(self, path: str) -> bool:
        if not self.is_configured:
            return False
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except ClientError as e:
            logger.error(f"R2 Delete failed for {path}: {e}")
            return False

    async def delete(self, path: str) -> bool:
        return await to_thread.run_sync(self._delete_sync, path)

    def _exists_sync(self, path: str) -> bool:
        if not self.is_configured:
            return False
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"R2 Exists check failed for {path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        return await to_thread.run_sync(self._exists_sync, path)

    def _get_signed_url_sync(self, path: str, expiration_sec: int, method: str) -> str:
        if not self.is_configured:
            return ""
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
        if not self.is_configured:
            return {"status": "not_configured", "error": "R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET must all be set"}
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

class S3StorageProvider(StorageProvider):
    def __init__(self):
        self.is_configured = False

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        raise NotImplementedError("S3 storage upload is not implemented.")

    async def download(self, path: str) -> Optional[bytes]:
        raise NotImplementedError("S3 storage download is not implemented.")

    async def delete(self, path: str) -> bool:
        raise NotImplementedError("S3 storage delete is not implemented.")

    async def exists(self, path: str) -> bool:
        raise NotImplementedError("S3 storage exists check is not implemented.")

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        raise NotImplementedError("S3 signed URL generation is not implemented.")

    async def health_check(self) -> dict:
        return {"status": "not_implemented", "provider": "s3"}

class GoogleCloudStorageProvider(StorageProvider):
    def __init__(self):
        self.is_configured = False

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        raise NotImplementedError("GCS upload is not implemented.")

    async def download(self, path: str) -> Optional[bytes]:
        raise NotImplementedError("GCS download is not implemented.")

    async def delete(self, path: str) -> bool:
        raise NotImplementedError("GCS delete is not implemented.")

    async def exists(self, path: str) -> bool:
        raise NotImplementedError("GCS exists check is not implemented.")

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        raise NotImplementedError("GCS signed URL generation is not implemented.")

    async def health_check(self) -> dict:
        return {"status": "not_implemented", "provider": "gcs"}

class AzureBlobStorageProvider(StorageProvider):
    def __init__(self):
        self.is_configured = False

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        raise NotImplementedError("Azure Blob upload is not implemented.")

    async def download(self, path: str) -> Optional[bytes]:
        raise NotImplementedError("Azure Blob download is not implemented.")

    async def delete(self, path: str) -> bool:
        raise NotImplementedError("Azure Blob delete is not implemented.")

    async def exists(self, path: str) -> bool:
        raise NotImplementedError("Azure Blob exists check is not implemented.")

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        raise NotImplementedError("Azure Blob signed URL generation is not implemented.")

    async def health_check(self) -> dict:
        return {"status": "not_implemented", "provider": "azure"}

class MinIOStorageProvider(StorageProvider):
    def __init__(self):
        self.is_configured = False

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        raise NotImplementedError("MinIO upload is not implemented.")

    async def download(self, path: str) -> Optional[bytes]:
        raise NotImplementedError("MinIO download is not implemented.")

    async def delete(self, path: str) -> bool:
        raise NotImplementedError("MinIO delete is not implemented.")

    async def exists(self, path: str) -> bool:
        raise NotImplementedError("MinIO exists check is not implemented.")

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        raise NotImplementedError("MinIO signed URL generation is not implemented.")

    async def health_check(self) -> dict:
        return {"status": "not_implemented", "provider": "minio"}


storage_provider = CloudflareR2Provider()

# Factory method to retrieve correct storage provider
def get_storage_provider(provider_type: str) -> StorageProvider:
    provider_type = provider_type.lower()
    if provider_type == "r2":
        return storage_provider
    elif provider_type == "s3":
        return S3StorageProvider()
    elif provider_type == "gcs":
        return GoogleCloudStorageProvider()
    elif provider_type == "azure":
        return AzureBlobStorageProvider()
    elif provider_type == "minio":
        return MinIOStorageProvider()
    else:
        raise ValueError(f"Unknown storage provider type: {provider_type}")
