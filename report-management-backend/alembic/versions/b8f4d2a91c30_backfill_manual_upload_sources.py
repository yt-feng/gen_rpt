"""Backfill source metadata for enterprise document uploads."""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "b8f4d2a91c30"
down_revision = "ff1a29b50845"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    documents = sa.table(
        "knowledge_documents",
        sa.column("id", sa.Uuid()),
        sa.column("language", sa.String()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    sources = sa.table(
        "knowledge_sources",
        sa.column("id", sa.Uuid()),
        sa.column("document_id", sa.Uuid()),
        sa.column("publisher", sa.String()),
        sa.column("source_type", sa.String()),
        sa.column("authority_score", sa.Float()),
        sa.column("trust_score", sa.Float()),
        sa.column("language", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    missing_documents = connection.execute(
        sa.select(documents.c.id, documents.c.language).where(
            documents.c.deleted_at.is_(None),
            ~sa.exists(sa.select(1).where(sources.c.document_id == documents.c.id)),
        )
    ).all()
    now = datetime.now(timezone.utc)
    if missing_documents:
        op.bulk_insert(
            sources,
            [
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "publisher": "Enterprise Upload Backfill",
                    "source_type": "manual_upload",
                    "authority_score": 0.6,
                    "trust_score": 1.0,
                    "language": language,
                    "created_at": now,
                    "updated_at": now,
                }
                for document_id, language in missing_documents
            ],
        )

    policies = sa.table(
        "validation_policies",
        sa.column("id", sa.Uuid()),
        sa.column("rules", sa.JSON()),
    )
    for policy_id, current_rules in connection.execute(
        sa.select(policies.c.id, policies.c.rules)
    ).all():
        rules = dict(current_rules or {})
        allowed = list(rules.get("allowed_source_types") or [])
        if "manual_upload" not in allowed:
            rules["allowed_source_types"] = [*allowed, "manual_upload"]
            connection.execute(
                policies.update().where(policies.c.id == policy_id).values(rules=rules)
            )

    # Cached empty/invalid packages were produced under the previous source
    # policy and must not survive deployment of the corrected classification.
    connection.execute(sa.text("DELETE FROM generation_context_caches"))


def downgrade() -> None:
    connection = op.get_bind()
    sources = sa.table(
        "knowledge_sources",
        sa.column("publisher", sa.String()),
        sa.column("source_type", sa.String()),
    )
    connection.execute(
        sources.delete().where(
            sources.c.publisher == "Enterprise Upload Backfill",
            sources.c.source_type == "manual_upload",
        )
    )

    policies = sa.table(
        "validation_policies",
        sa.column("id", sa.Uuid()),
        sa.column("rules", sa.JSON()),
    )
    for policy_id, current_rules in connection.execute(
        sa.select(policies.c.id, policies.c.rules)
    ).all():
        rules = dict(current_rules or {})
        allowed = list(rules.get("allowed_source_types") or [])
        if "manual_upload" in allowed:
            rules["allowed_source_types"] = [
                source_type for source_type in allowed if source_type != "manual_upload"
            ]
            connection.execute(
                policies.update().where(policies.c.id == policy_id).values(rules=rules)
            )
