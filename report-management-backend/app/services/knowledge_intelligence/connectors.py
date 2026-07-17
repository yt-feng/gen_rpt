from typing import Dict, Any, List

class ConnectorFrameworkService:
    async def get_connectors(self) -> Dict[str, Any]:
        return {
            "notice": "Connector framework is planned for a future release. Manual upload via the Knowledge dashboard is the supported ingestion method.",
            "connectors": [
                {
                    "connector_id": "sharepoint_default",
                    "name": "Microsoft SharePoint",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["auto_sync", "delta_indexing", "permission_mapping"],
                    "auth_type": "OAuth2"
                },
                {
                    "connector_id": "confluence_default",
                    "name": "Atlassian Confluence",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["space_filtering", "page_history", "tags_sync"],
                    "auth_type": "API Token"
                },
                {
                    "connector_id": "google_drive_default",
                    "name": "Google Drive",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["folder_tracking", "doc_conversion", "real_time_sync"],
                    "auth_type": "OAuth2"
                },
                {
                    "connector_id": "notion_default",
                    "name": "Notion Integration",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["database_sync", "page_embedding"],
                    "auth_type": "OAuth2 / Integration Token"
                },
                {
                    "connector_id": "onedrive_default",
                    "name": "Microsoft OneDrive",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["personal_drives", "corporate_shares"],
                    "auth_type": "OAuth2"
                },
                {
                    "connector_id": "s3_default",
                    "name": "Amazon S3",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["bucket_notification", "metadata_scraping"],
                    "auth_type": "IAM Role"
                },
                {
                    "connector_id": "azure_blob_default",
                    "name": "Azure Blob Storage",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["container_sync"],
                    "auth_type": "Shared Access Signature"
                },
                {
                    "connector_id": "github_default",
                    "name": "GitHub Repositories",
                    "status": "not_implemented",
                    "available_in_future_phase": True,
                    "supported_features": ["markdown_indexing", "issue_knowledge"],
                    "auth_type": "Personal Access Token"
                }
            ]
        }

connector_framework_service = ConnectorFrameworkService()
