"""Real SQLAlchemy/SQLite execution of the exact backend conditional claim.

No backend imports, credentials, network, provider calls or external database.
CI requires SQLAlchemy; local environments without it explicitly skip SQL tests.
"""
from copy import deepcopy
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from tests.test_generation_retry_context import (
    RetryHarness, ClaimConflict, Status, exact_method, evidence, module,
)

try:
    import sqlalchemy as sa
except ModuleNotFoundError:
    sa = None


class SqlDependencyContract(unittest.TestCase):
    def test_ci_has_real_sqlalchemy_dependency(self):
        if os.environ.get("REQUIRE_SQLALCHEMY_CONTRACT") == "1":
            self.assertIsNotNone(sa, "CI must install SQLAlchemy; claim integration must not be silently skipped")


@unittest.skipIf(sa is None, "SQLAlchemy unavailable locally; required in CI")
class SqlClaimContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite:///:memory:")
        self.meta = sa.MetaData()
        self.table = sa.Table("generation_claims", self.meta,
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("created_by", sa.Uuid), sa.Column("document_id", sa.Uuid),
            sa.Column("prompt", sa.String), sa.Column("topic", sa.String), sa.Column("report_type", sa.String),
            sa.Column("audit_metadata", sa.JSON), sa.Column("retry_count", sa.Integer),
            sa.Column("status", sa.String), sa.Column("errors", sa.String),
            sa.Column("completed", sa.String), sa.Column("duration", sa.Integer),
        )
        for col in self.table.c: setattr(self.table, col.name, col)
        self.meta.create_all(self.engine)
        h = RetryHarness()
        self.original = {col.name: deepcopy(getattr(h.job, col.name)) for col in self.table.c}
        with self.engine.begin() as conn: conn.execute(self.table.insert().values(**self.original))
        self.cache = NS(set_cached_context=AsyncMock())

    def tearDown(self):
        self.engine.dispose()

    async def claim(self, mutate_orm=None):
        h = RetryHarness()
        binding = h.service._job_context_binding(h.job)
        binding["slug"] = h.document.slug
        if mutate_orm: mutate_orm(h.job)
        claim = exact_method("services/generation.py", "_claim_prepared_job", {
            "GenerationJob": self.table, "update": sa.update, "GenerationClaimConflict": ClaimConflict,
            "JobStatusType": Status, "settings": h.settings,
        }, "GenerationService")
        with self.engine.connect() as conn:
            async def execute(statement): return conn.execute(statement)
            async def commit(): conn.commit()
            async def rollback(): conn.rollback()
            async def refresh(job):
                row = conn.execute(sa.select(self.table).where(self.table.c.id == binding["id"])).mappings().one()
                for name, value in row.items(): setattr(job, name, value)
            db = NS(execute=execute, commit=commit, rollback=rollback, refresh=refresh)
            with patch.dict("sys.modules", {"app.services.rag_integration": module(generation_context_service=NS(cache_service=self.cache))}):
                won = await claim(h.service, db, h.job, h.document, deepcopy(h.job.audit_metadata), evidence(),
                                  binding=binding, expected_status=Status.failed,
                                  original_retry_count=0, target_status=Status.pending, increment_retry=True)
            if won: await h.worker.dispatch(h.job)
        return h

    async def test_two_prepared_claims_publish_and_dispatch_only_once(self):
        winner = await self.claim()
        with self.assertRaises(ClaimConflict): await self.claim()
        self.cache.set_cached_context.assert_awaited_once()
        winner.worker.dispatch.assert_awaited_once()
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(self.table)).mappings().one()
        self.assertEqual(row["retry_count"], 1); self.assertEqual(row["status"], "pending")

    async def test_actor_document_query_scope_or_count_change_rejects_claim(self):
        changes = [{"created_by": uuid.UUID(int=99)}, {"document_id": uuid.UUID(int=99)},
                   {"prompt": "changed source query"}, {"topic": "changed topic"},
                   {"audit_metadata": {"rag_required": False}}, {"retry_count": 1}, {"report_type": "bulk"}]
        for change in changes:
            with self.subTest(change=change):
                with self.engine.begin() as conn:
                    conn.execute(self.table.update().values(**self.original))
                    conn.execute(self.table.update().values(**change))
                with self.assertRaises(ClaimConflict): await self.claim()
        self.cache.set_cached_context.assert_not_awaited()

    async def test_changed_orm_identity_cannot_follow_a_different_database_actor(self):
        with self.engine.begin() as conn:
            conn.execute(self.table.update().values(created_by=uuid.UUID(int=99)))
        with self.assertRaises(ClaimConflict):
            await self.claim(lambda job: setattr(job, "created_by", uuid.UUID(int=99)))
        self.cache.set_cached_context.assert_not_awaited()
