"""Execute exact backend methods with isolated DB/provider fakes.

No backend app/config imports, credentials, network, model calls or external DB.
The expression fake exercises conditional claim predicates and call ordering;
this is not a substitute for deployment/database integration acceptance.
"""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "report-management-backend/app"
ACTOR, COLLECTION, DOC, CHUNK, JOB = [uuid.UUID(int=i) for i in range(1, 6)]


def exact_method(relative, name, namespace, cls=None):
    path = APP / relative
    tree = ast.parse(path.read_text())
    scope = tree.body if cls is None else next(n.body for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    node = deepcopy(next(n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name))
    node.decorator_list = []
    # Strip only FastAPI dependency defaults; method defaults stay exact.
    node.args.defaults = [ast.Constant(None) if isinstance(n, ast.Call) else n for n in node.args.defaults]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def module(**values):
    result = ModuleType("isolated_test_dependency")
    result.__dict__.update(values)
    return result


class Column:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return lambda row: getattr(row, self.name) == value
    def __gt__(self, value): return lambda row: getattr(row, self.name) > value
    def is_(self, value): return self == value
    def in_(self, values): return lambda row: getattr(row, self.name) in values
    def asc(self): return self


class Statement:
    def __init__(self, model, *_): self.model, self.conditions, self.changes = model, [], {}
    def where(self, *conditions): self.conditions.extend(conditions); return self
    filter = where
    def values(self, **changes): self.changes = changes; return self
    def returning(self, *_): return self
    def execution_options(self, **_): return self
    def options(self, *_): return self
    def join(self, *_): return self
    def order_by(self, *_): return self
    def limit(self, value): self.limit_value = value; return self


class Status(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ClaimConflict(ValueError):
    pass


class PreparationError(RuntimeError):
    def __init__(self, stage): self.stage = stage


JOB_MODEL = NS(**{name: Column(name) for name in ["id", "status", "retry_count", "created_by", "document_id", "report_type", "started", "prompt", "topic", "audit_metadata"]})
DOC_MODEL, USER_MODEL = NS(id=Column("id")), object()
COL_MODEL = NS(**{name: Column(name) for name in ["id", "deleted_at", "status", "organization_id", "owner_id"]})
PERM_MODEL = NS(**{name: Column(name) for name in ["collection_id", "user_id"]})


def evidence():
    return {"validated_chunks": [{"chunk_id": str(CHUNK), "document_id": str(DOC),
             "text": "Synthetic validated local regression evidence.", "validation_status": "validated"}],
            "context_metadata": {"estimated_tokens": 22},
            "knowledge_snapshot": {"collections": [str(COLLECTION)]},
            "knowledge_snapshot_id": str(uuid.UUID(int=6)), "validation_report_reference": str(uuid.UUID(int=7))}


class RetryHarness:
    def __init__(self):
        self.events = []
        self.job = NS(id=JOB, document_id=DOC, created_by=ACTOR, status=Status.failed,
                      report_type="standard", retry_count=0, topic="Synthetic test topic", prompt="Original query",
                      errors="prior failure", completed=None, duration=None,
                      audit_metadata={"rag_required": True, "rag": {"requested": True, "collection_ids": [str(COLLECTION)], "chunk_count": 1}})
        self.document = NS(id=DOC, slug="synthetic-report", created_by=ACTOR, owner_id=None)
        self.actor = NS(id=ACTOR, status="active")
        self.package = evidence()
        self.cache = {"old": True}
        self.claim_hook = None
        self.context = NS(prepare_context=AsyncMock(side_effect=self.prepare),
                          cache_service=NS(set_cached_context=AsyncMock(side_effect=self.publish)))
        self.worker = NS(dispatch=AsyncMock(side_effect=self.dispatch))
        self.db = NS(get=AsyncMock(side_effect=self.get), execute=AsyncMock(side_effect=self.execute),
                     commit=AsyncMock(side_effect=lambda: self.events.append("commit")),
                     rollback=AsyncMock(side_effect=lambda: self.events.append("rollback")), refresh=AsyncMock())
        self.poll = Mock(return_value="poll-task")
        self.tasks = Mock()
        self.settings = NS(RAG_ENABLED=True, RAG_CONTEXT_CACHE_TTL_SECONDS=14400)
        namespace = {
            "uuid": uuid, "settings": self.settings, "JobStatusType": Status,
            "GenerationJob": JOB_MODEL, "update": Statement,
            "asyncio": NS(create_task=self.tasks), "poll_r2_for_completion": self.poll,
            "GenerationClaimConflict": ClaimConflict,
        }
        methods = {name: exact_method("services/generation.py", name, namespace, "GenerationService")
                   for name in ["retry_job", "_prepare_job_context", "_claim_prepared_job", "_job_context_binding"]}
        self.service = type("Service", (), methods)()
        self.service.get_job = AsyncMock(return_value=self.job)
        self.service.worker = self.worker
        self.imports = {"app.models.document": module(Document=DOC_MODEL),
                        "app.models.identity": module(User=USER_MODEL),
                        "app.services.rag_integration": module(generation_context_service=self.context, RAGContextPreparationError=PreparationError)}

    async def get(self, model, _):
        return self.document if model is DOC_MODEL else self.actor

    async def prepare(self, **kwargs):
        self.events.append("prepare")
        # The real pipeline commits validation/snapshot/analytics here.
        await self.db.commit()
        return deepcopy(self.package)

    async def publish(self, db, key, package, **kwargs):
        self.events.append("publish")
        self.cache = deepcopy(package)
        await db.commit()

    async def dispatch(self, job):
        self.events.append("dispatch")
        return True

    async def execute(self, statement):
        self.events.append("claim")
        if self.claim_hook:
            self.claim_hook()
        matched = all(condition(self.job) for condition in statement.conditions)
        if matched:
            for name, value in statement.changes.items(): setattr(self.job, name, value)
        return NS(scalar_one_or_none=lambda: JOB if matched else None)

    async def run(self, actor=ACTOR):
        with patch.dict(sys.modules, self.imports):
            return await self.service.retry_job(self.db, JOB, actor_id=actor)


class GenerationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_context_reprepared_once_before_claim_publish_dispatch(self):
        h = RetryHarness()
        result = await h.run()
        self.assertEqual(result.status, Status.pending)
        self.assertEqual(result.retry_count, 1)
        h.context.prepare_context.assert_awaited_once_with(
            db=h.db, query="Original query", collection_ids=[COLLECTION], user_id=ACTOR,
            user_org_id=None, generation_job_id=JOB, slug="synthetic-report",
            force_refresh=True, cache_result=False)
        self.assertLess(h.events.index("prepare"), h.events.index("claim"))
        self.assertLess(h.events.index("claim"), h.events.index("publish"))
        self.assertLess(h.events.index("publish"), h.events.index("dispatch"))
        self.assertTrue(result.audit_metadata["rag_required"])
        self.assertEqual(h.cache, h.package)
        h.context.cache_service.set_cached_context.assert_awaited_once_with(
            h.db, "context:slug:synthetic-report", h.package, ttl_seconds=14400)
        h.worker.dispatch.assert_awaited_once_with(result)
        h.poll.assert_called_once_with(JOB)
        h.tasks.assert_called_once_with("poll-task")

    async def test_missing_or_changed_actor_document_state_stops_before_preparation(self):
        variants = [lambda h: setattr(h.job, "created_by", None),
                    lambda h: setattr(h, "actor", None),
                    lambda h: setattr(h.actor, "status", "disabled"),
                    lambda h: setattr(h, "document", None),
                    lambda h: setattr(h.document, "slug", ""),
                    lambda h: setattr(h.document, "owner_id", uuid.UUID(int=99)),
                    lambda h: setattr(h.document, "created_by", None),
                    lambda h: setattr(h.job, "status", Status.running),
                    lambda h: setattr(h.job, "status", Status.pending),
                    lambda h: setattr(h.job, "status", Status.completed)]
        for change in variants:
            h = RetryHarness(); change(h)
            with self.subTest(change=change), self.assertRaises(ValueError): await h.run()
            h.context.prepare_context.assert_not_awaited(); h.worker.dispatch.assert_not_awaited()
        for actor in [uuid.UUID(int=0), uuid.UUID(int=99), None]:
            h = RetryHarness()
            with self.subTest(actor=actor), self.assertRaises(ValueError): await h.run(actor)
            h.context.prepare_context.assert_not_awaited()

    async def test_missing_malformed_duplicate_scope_cannot_broaden(self):
        for raw in [None, [], "all", ["bad"], [str(uuid.UUID(int=0))], [str(COLLECTION)] * 2]:
            h = RetryHarness(); h.job.audit_metadata["rag"]["collection_ids"] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError): await h.run()
            h.context.prepare_context.assert_not_awaited(); h.db.execute.assert_not_awaited()
            self.assertEqual(h.job.retry_count, 0)

    async def test_preparation_failure_and_no_evidence_never_claim_or_dispatch(self):
        for failure in [PreparationError("retrieval"), PreparationError("validation"), RuntimeError("private sentinel")]:
            h = RetryHarness(); h.context.prepare_context.side_effect = failure
            with self.subTest(failure=type(failure)), self.assertRaises(ValueError) as caught: await h.run()
            self.assertNotIn("private sentinel", str(caught.exception))
            h.db.execute.assert_not_awaited(); h.worker.dispatch.assert_not_awaited(); h.tasks.assert_not_called()
            self.assertEqual(h.job.status, Status.failed); self.assertEqual(h.job.retry_count, 0)
        for package in [{"validated_chunks": []}, {"validated_chunks": [{}]}, None]:
            h = RetryHarness(); h.package = package
            with self.subTest(package=package), self.assertRaises(ValueError): await h.run()
            h.db.execute.assert_not_awaited(); self.assertEqual(h.cache, {"old": True})

    async def test_wrong_scope_or_missing_validation_provenance_is_rejected(self):
        for key, value in [("knowledge_snapshot", {"collections": [str(uuid.UUID(int=99))]}),
                           ("knowledge_snapshot_id", None), ("validation_report_reference", None)]:
            h = RetryHarness(); h.package[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): await h.run()
            h.db.execute.assert_not_awaited(); h.worker.dispatch.assert_not_awaited()

    async def test_disabled_rag_and_missing_query_do_not_dispatch(self):
        for disabled in [True, False]:
            h = RetryHarness()
            if disabled: h.settings.RAG_ENABLED = False
            else: h.job.prompt = h.job.topic = ""
            with self.assertRaises(ValueError): await h.run()
            h.context.prepare_context.assert_not_awaited(); h.worker.dispatch.assert_not_awaited()

    async def test_grounded_legacy_false_becomes_required_but_explicit_web_only_stays_false(self):
        h = RetryHarness(); h.job.audit_metadata["rag_required"] = False
        await h.run(); self.assertTrue(h.job.audit_metadata["rag_required"])
        h = RetryHarness(); h.job.audit_metadata = {"rag_required": False, "rag": {"source_policy": "public_only", "requested": False, "chunk_count": 0, "collection_ids": []}}
        await h.run(); h.context.prepare_context.assert_not_awaited()
        self.assertFalse(h.job.audit_metadata["rag_required"]); h.worker.dispatch.assert_awaited_once()

    async def test_concurrent_loser_never_overwrites_winner_context_or_dispatches(self):
        for winner_status in [Status.pending, Status.failed]:
            h = RetryHarness()
            def winner():
                h.job.status = winner_status; h.job.retry_count = 1
                h.cache = {"winner": "different current validated package"}
            h.claim_hook = winner
            with self.subTest(status=winner_status), self.assertRaisesRegex(ValueError, "Job changed"): await h.run()
            h.worker.dispatch.assert_not_awaited(); h.tasks.assert_not_called()
            h.context.cache_service.set_cached_context.assert_not_awaited()
            self.assertEqual(h.cache, {"winner": "different current validated package"})
            h.db.rollback.assert_awaited_once()

    async def test_cache_publication_failure_never_dispatches(self):
        h = RetryHarness(); h.context.cache_service.set_cached_context.side_effect = RuntimeError("private sentinel")
        result = await h.run()
        self.assertEqual(result.status, Status.failed)
        self.assertIn("cache publication failed", result.errors)
        h.worker.dispatch.assert_not_awaited(); h.tasks.assert_not_called()

    async def test_false_or_raised_dispatch_fails_once_without_poller_or_replay(self):
        for failure in [False, RuntimeError("private sentinel")]:
            h = RetryHarness()
            h.worker.dispatch.side_effect = failure if isinstance(failure, Exception) else None
            h.worker.dispatch.return_value = failure
            result = await h.run()
            self.assertEqual(result.status, Status.failed); self.assertEqual(result.retry_count, 1)
            self.assertNotIn("private sentinel", result.errors)
            h.worker.dispatch.assert_awaited_once(); h.poll.assert_not_called(); h.tasks.assert_not_called()

    async def test_endpoint_passes_request_actor(self):
        service = NS(retry_job=AsyncMock(return_value=NS(id=JOB, status=Status.pending)))
        endpoint = exact_method("api/v1/endpoints/generation.py", "retry_job", {
            "UUID": uuid.UUID, "generation_service": service, "success_response": lambda **kw: kw,
        })
        db = object()
        await endpoint(JOB, db, {"id": str(ACTOR)})
        service.retry_job.assert_awaited_once_with(db, JOB, actor_id=ACTOR)


class FreshEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_permission_refresh_ignores_positive_cache_and_observes_revoke(self):
        cache = NS(get=AsyncMock(return_value=True), set=AsyncMock())
        columns = [NS(id=COLLECTION, deleted_at=None, owner_id=uuid.UUID(int=99), visibility="private")]
        rows = [columns, []]
        async def execute(statement):
            candidates = rows.pop(0)
            return NS(scalars=lambda: NS(all=lambda: [row for row in candidates if all(c(row) for c in statement.conditions)]))
        function = exact_method("services/knowledge_permission.py", "batch_check_permissions", {
            "knowledge_cache_service": cache, "select": Statement, "KnowledgeCollection": COL_MODEL,
            "CollectionPermission": PERM_MODEL, "PERMISSION_LEVELS": {"viewer": 10},
        }, "KnowledgePermissionService")
        self.assertEqual(await function(None, NS(execute=execute), [COLLECTION], ACTOR, "viewer", force_refresh=True), set())
        cache.get.assert_not_awaited(); cache.set.assert_awaited_once_with(f"permission:{COLLECTION}:{ACTOR}:viewer", False)

    async def test_normal_permission_lookup_keeps_existing_cache_behavior(self):
        cache = NS(get=AsyncMock(return_value=True), set=AsyncMock())
        function = exact_method("services/knowledge_permission.py", "batch_check_permissions", {"knowledge_cache_service": cache}, "KnowledgePermissionService")
        db = NS(execute=AsyncMock())
        self.assertEqual(await function(None, db, [COLLECTION], ACTOR, "viewer"), {COLLECTION})
        db.execute.assert_not_awaited()

    async def retrieval(self, collections, permitted, force=True):
        permissions = NS(batch_check_permissions=AsyncMock(return_value=permitted))
        cache = NS(get=AsyncMock(return_value={"chunks": ["stale source"]}))
        calls = []
        async def execute(statement):
            calls.append(statement)
            candidates = collections if statement.model is COL_MODEL else []
            return NS(scalars=lambda: NS(all=lambda: [row for row in candidates if all(c(row) for c in statement.conditions)]))
        docs = NS(**{name: Column(name) for name in ["deleted_at", "collection_id", "processing_status", "validation_status"]}, tags=object(), sources=object())
        function = exact_method("services/retrieval_engine.py", "retrieve_knowledge", {
            "time": time, "uuid": uuid, "select": Statement, "KnowledgeCollection": COL_MODEL,
            "KnowledgeDocument": docs, "selectinload": lambda _: None,
            "knowledge_permission_service": permissions, "knowledge_cache_service": cache,
            "hashlib": hashlib, "json": json,
        }, "RetrievalEngineService")
        result = await function(None, NS(execute=execute), "Original", collection_ids=[COLLECTION], user_id=ACTOR, force_refresh=force)
        return result, permissions, cache, calls

    async def test_fresh_retrieval_bypasses_stale_document_cache_with_exact_scope(self):
        col = NS(id=COLLECTION, deleted_at=None, status="active")
        result, permissions, cache, calls = await self.retrieval([col], {COLLECTION})
        self.assertEqual(result["chunks"], [])  # removed documents cannot come from retrieval cache
        self.assertEqual(result["snapshot"]["collections"], [str(COLLECTION)])
        permissions.batch_check_permissions.assert_awaited_once()
        self.assertTrue(permissions.batch_check_permissions.call_args.kwargs["force_refresh"])
        cache.get.assert_not_awaited(); self.assertEqual(len(calls), 2)

    async def test_missing_inactive_or_revoked_collection_cannot_silently_narrow(self):
        for cols, perms in [([], set()), ([NS(id=COLLECTION, deleted_at=None, status="archived")], {COLLECTION}),
                            ([NS(id=COLLECTION, deleted_at=None, status="active")], set())]:
            with self.subTest(cols=cols, perms=perms), self.assertRaises(ValueError): await self.retrieval(cols, perms)

    async def prepare(self, force, cache_result=True, validation_failure=False):
        chunk = NS(chunk_id=CHUNK, document_id=DOC, text="Synthetic current evidence", confidence=0.8,
                   authority=0.8, is_duplicate=False, conflicts_with=[], validation_status="validated", metadata={})
        val = NS(evidence_ranking=[CHUNK], validated_chunks=[chunk], validated_sources=[],
                 document_references=[{"document_id": str(DOC), "file_name": "synthetic.txt"}],
                 confidence_scores={}, authority_scores={}, knowledge_snapshot={"collections": [str(COLLECTION)]},
                 validation_report_reference=str(uuid.UUID(int=7)), collection_metadata={}, context_metadata={})
        retrieval = NS(retrieve_knowledge=AsyncMock(return_value={"chunks": ["fresh"], "session_id": uuid.UUID(int=8), "snapshot": {"collections": [str(COLLECTION)]}}))
        validation = NS(validate_session=AsyncMock(return_value=val))
        if validation_failure: validation.validate_session.side_effect = RuntimeError("private sentinel")
        service = NS(cache_service=NS(get_cached_context=AsyncMock(return_value={"cached": True}), set_cached_context=AsyncMock()),
                     snapshot_service=NS(create_snapshot=AsyncMock(return_value=NS(id=uuid.UUID(int=6)))),
                     analytics_service=NS(log_analytics=AsyncMock()))
        compiler = exact_method("services/retrieval_context.py", "build_validated_context", {
            "hashlib": hashlib, "estimate_tokens": lambda text: max(1, len(text) // 4)})
        function = exact_method("services/rag_integration.py", "prepare_context", {
            "time": time, "uuid": uuid, "hashlib": hashlib,
            "settings": NS(RAG_CONTEXT_TOKEN_BUDGET=6000, RAG_CONTEXT_CACHE_TTL_SECONDS=14400, APP_ENV="test"),
            "retrieval_engine_service": retrieval, "validation_service": validation,
            "RAGContextPreparationError": PreparationError, "build_validated_context": compiler,
            "stringify_uuids": lambda value: json.loads(json.dumps(value, default=str)), "logger": Mock(),
        }, "GenerationContextService")
        if validation_failure:
            with self.assertRaises(PreparationError) as caught:
                await function(service, object(), "query", [COLLECTION], ACTOR, slug="synthetic-report", force_refresh=force, cache_result=cache_result)
            self.assertEqual(caught.exception.stage, "validation"); result = None
        else:
            result = await function(service, object(), "query", [COLLECTION], ACTOR, slug="synthetic-report", force_refresh=force, cache_result=cache_result)
        return result, service, retrieval, validation

    async def test_prepare_refresh_runs_real_compiler_and_validation_without_cache_publish(self):
        result, service, retrieval, validation = await self.prepare(True, False)
        self.assertEqual(result["validated_chunks"][0]["text"], "Synthetic current evidence")
        service.cache_service.get_cached_context.assert_not_awaited()
        service.cache_service.set_cached_context.assert_not_awaited()
        self.assertTrue(retrieval.retrieve_knowledge.call_args.kwargs["force_refresh"])
        validation.validate_session.assert_awaited_once()
        service.snapshot_service.create_snapshot.assert_awaited_once()

    async def test_existing_prepare_default_still_reuses_valid_cache(self):
        result, service, retrieval, validation = await self.prepare(False)
        self.assertEqual(result, {"cached": True})
        retrieval.retrieve_knowledge.assert_not_awaited(); validation.validate_session.assert_not_awaited()

    async def test_validation_failure_never_renews_old_context(self):
        _, service, _, validation = await self.prepare(True, False, True)
        validation.validate_session.assert_awaited_once()
        service.cache_service.set_cached_context.assert_not_awaited()
        service.snapshot_service.create_snapshot.assert_not_awaited()


class BulkAndProducerTests(unittest.IsolatedAsyncioTestCase):
    async def bulk(self, h):
        h.job.status = Status.pending; h.job.report_type = "bulk"
        count_marker = object()
        async def execute(statement):
            if statement.changes: return await h.execute(statement)
            if statement.model is count_marker: return NS(scalar=lambda: getattr(h, "running_count", 0))
            return NS(all=lambda: [(h.job, h.document)])
        h.db.execute = AsyncMock(side_effect=execute)
        if not hasattr(h.worker, "dispatch_bulk"):
            h.worker.dispatch_bulk = AsyncMock(return_value=True)
        fake_asyncio = module(create_task=h.tasks, sleep=AsyncMock())
        fake_sa = module(select=Statement, func=NS(count=lambda _: count_marker))
        imports = {**h.imports, "app.storage.provider": module(storage_provider=NS(download=AsyncMock(return_value=json.dumps({"paused": getattr(h, "bulk_paused", False), "limit": 20}).encode()))),
                   "app.models.enums": module(JobStatusType=Status), "asyncio": fake_asyncio}
        function = exact_method("services/generation.py", "process_bulk_queue", {
            "GenerationJob": JOB_MODEL, "select": Statement, "update": Statement,
            "func": fake_sa.func, "poll_r2_for_completion": h.poll, "GenerationClaimConflict": ClaimConflict,
        }, "GenerationService")
        with patch.dict(sys.modules, imports): await function(h.service, h.db)
        return fake_asyncio

    async def test_failed_bulk_retry_queues_without_prepare_when_paused_or_full(self):
        for paused, running in [(True, 0), (False, 20)]:
            h = RetryHarness(); h.job.report_type = "bulk"
            h.bulk_paused = paused; h.running_count = running
            h.service.process_bulk_queue = AsyncMock(side_effect=lambda db: self.bulk(h))
            # AsyncMock does not await a coroutine returned by a synchronous side effect.
            async def consume(db): await self.bulk(h)
            h.service.process_bulk_queue.side_effect = consume
            await h.run()
            self.assertEqual(h.job.status, Status.pending); self.assertEqual(h.job.retry_count, 1)
            h.context.prepare_context.assert_not_awaited()
            h.context.cache_service.set_cached_context.assert_not_awaited()
            h.worker.dispatch.assert_not_awaited(); h.worker.dispatch_bulk.assert_not_awaited()
            h.service.process_bulk_queue.assert_awaited_once()

    async def test_failed_bulk_retry_with_capacity_prepares_once_via_original_queue(self):
        h = RetryHarness(); h.job.report_type = "bulk"
        async def consume(db): await self.bulk(h)
        h.service.process_bulk_queue = AsyncMock(side_effect=consume)
        await h.run()
        self.assertEqual(h.job.status, Status.running); self.assertEqual(h.job.retry_count, 1)
        h.context.prepare_context.assert_awaited_once()
        h.worker.dispatch.assert_not_awaited()
        h.worker.dispatch_bulk.assert_awaited_once_with(slug="synthetic-report", topic="Synthetic test topic", rag_required=True)
        h.context.cache_service.set_cached_context.assert_awaited_once()

    async def test_failed_bulk_retry_loser_never_calls_queue_or_publishes(self):
        h = RetryHarness(); h.job.report_type = "bulk"
        h.service.process_bulk_queue = AsyncMock()
        def winner(): h.job.status = Status.pending; h.job.retry_count = 1
        h.claim_hook = winner
        with self.assertRaises(ClaimConflict): await h.run()
        h.service.process_bulk_queue.assert_not_awaited()
        h.context.prepare_context.assert_not_awaited(); h.context.cache_service.set_cached_context.assert_not_awaited()
        h.worker.dispatch.assert_not_awaited()

    async def test_bulk_preserves_router_single_prepare_and_queue_attempt_count(self):
        h = RetryHarness(); sleeps = await self.bulk(h)
        h.context.prepare_context.assert_awaited_once()
        h.worker.dispatch.assert_not_awaited()
        h.worker.dispatch_bulk.assert_awaited_once_with(slug="synthetic-report", topic="Synthetic test topic", rag_required=True)
        self.assertEqual(h.job.status, Status.running); self.assertEqual(h.job.retry_count, 0)
        self.assertLess(h.events.index("claim"), h.events.index("publish"))
        sleeps.sleep.assert_awaited_once_with(2.0)
        h.tasks.assert_called_once()

    async def test_bulk_missing_actor_scope_and_validation_failure_are_failed_without_dispatch(self):
        for change in [lambda h: setattr(h.job, "created_by", None),
                       lambda h: setattr(h.actor, "status", "disabled"),
                       lambda h: h.job.audit_metadata["rag"].update(collection_ids=[]),
                       lambda h: setattr(h.context.prepare_context, "side_effect", PreparationError("validation"))]:
            h = RetryHarness(); change(h)
            with self.subTest(change=change): await self.bulk(h)
            self.assertEqual(h.job.status, Status.failed)
            h.worker.dispatch_bulk.assert_not_awaited(); h.tasks.assert_not_called()
            h.context.cache_service.set_cached_context.assert_not_awaited()

    async def test_bulk_false_and_raised_dispatch_mark_failed_without_poller(self):
        for fail in [False, RuntimeError("private sentinel")]:
            h = RetryHarness(); h.worker.dispatch_bulk = AsyncMock(return_value=False)
            if isinstance(fail, Exception): h.worker.dispatch_bulk.side_effect = fail
            await self.bulk(h)
            self.assertEqual(h.job.status, Status.failed)
            h.worker.dispatch_bulk.assert_awaited_once(); h.tasks.assert_not_called()

    async def test_bulk_explicit_public_policy_survives_but_legacy_false_is_not_public_proof(self):
        h = RetryHarness(); h.job.audit_metadata = {"rag_required": False, "rag": {
            "source_policy": "public_only", "requested": False, "chunk_count": 0, "collection_ids": []}}
        await self.bulk(h); h.context.prepare_context.assert_not_awaited()
        h.worker.dispatch_bulk.assert_awaited_once_with(slug="synthetic-report", topic="Synthetic test topic", rag_required=False)
        for metadata in [{"rag_required": False}, {"rag_required": False, "rag": {}}]:
            h = RetryHarness(); h.job.audit_metadata = metadata
            with self.assertRaisesRegex(ValueError, "scope is missing"): await h.run()
            h.worker.dispatch.assert_not_awaited()
            await self.bulk(h)
            self.assertEqual(h.job.status, Status.failed); h.worker.dispatch_bulk.assert_not_awaited()

    async def test_bulk_losing_claim_does_not_fail_winner_or_write_cache(self):
        h = RetryHarness()
        def winner(): h.job.status = Status.running; h.cache = {"winner": True}
        h.claim_hook = winner
        await self.bulk(h)
        self.assertEqual(h.job.status, Status.running); self.assertEqual(h.cache, {"winner": True})
        h.context.cache_service.set_cached_context.assert_not_awaited(); h.worker.dispatch_bulk.assert_not_awaited()

    async def test_original_actor_document_query_and_scope_cannot_change_during_prepare(self):
        for field, value in [("created_by", uuid.UUID(int=99)), ("document_id", uuid.UUID(int=99)),
                             ("prompt", "different query"), ("audit_metadata", {"rag_required": False})]:
            h = RetryHarness()
            async def changed(**kwargs):
                setattr(h.job, field, value)
                return deepcopy(h.package)
            h.context.prepare_context.side_effect = changed
            with self.subTest(field=field), self.assertRaises(ClaimConflict): await h.run()
            h.worker.dispatch.assert_not_awaited(); h.context.cache_service.set_cached_context.assert_not_awaited()

    async def test_report_type_change_cannot_switch_the_original_dispatch_route(self):
        h = RetryHarness()
        async def changed(**kwargs):
            h.job.report_type = "bulk"
            return deepcopy(h.package)
        h.context.prepare_context.side_effect = changed
        with self.assertRaises(ClaimConflict): await h.run()
        h.worker.dispatch.assert_not_awaited()
        h.context.cache_service.set_cached_context.assert_not_awaited()

    async def test_new_request_scope_distinguishes_omitted_empty_and_explicit(self):
        function = exact_method("api/v1/endpoints/generation.py", "_resolve_generation_scope", {})
        cols = NS(scalars=lambda: NS(all=lambda: [COLLECTION]))
        imports = {"app.models.identity": module(User=USER_MODEL),
                   "app.models.knowledge": module(KnowledgeCollection=COL_MODEL), "sqlalchemy": module(select=Statement)}
        for requested, expected, reads in [(None, [COLLECTION], 1), ([], [], 0), ([COLLECTION], [COLLECTION], 0)]:
            db = NS(get=AsyncMock(return_value=NS(status="active")), execute=AsyncMock(return_value=cols))
            with patch.dict(sys.modules, imports): actual = await function(db, ACTOR, requested)
            self.assertEqual(actual, expected); self.assertEqual(db.execute.await_count, reads)

    async def test_initial_standard_and_bulk_save_scope_and_validation_before_dispatch(self):
        rag = {"requested": True, "collection_ids": [str(COLLECTION)], "chunk_count": 1,
               "validation_report_reference": "synthetic-validation", "knowledge_snapshot_id": "synthetic-snapshot"}
        for name in ["create_job", "create_bulk_job"]:
            seen = []
            def new_job(**kwargs): return NS(**kwargs)
            db = NS(add=lambda job: seen.append(job), commit=AsyncMock(), refresh=AsyncMock())
            async def dispatch(*args, **kwargs):
                self.assertEqual(seen[0].audit_metadata["rag"], rag)
                self.assertGreaterEqual(db.commit.await_count, 1)
                return True
            worker = NS(dispatch=AsyncMock(side_effect=dispatch), dispatch_bulk=AsyncMock(side_effect=dispatch))
            function = exact_method("services/generation.py", name, {
                "uuid": uuid, "GenerationJob": new_job, "JobStatusType": Status,
                "datetime": datetime, "timezone": timezone, "asyncio": NS(create_task=Mock()),
                "poll_r2_for_completion": Mock(return_value="poll"),
            }, "GenerationService")
            kwargs = dict(db=db, document_id=DOC, topic="Synthetic", created_by=ACTOR, rag_metadata=rag)
            if name == "create_job": kwargs.update(prompt="Original", report_type="standard", rag_required=True)
            else: kwargs.update(slug="synthetic-report")
            await function(NS(worker=worker), **kwargs)

    def test_prepared_metadata_retains_actual_provenance_without_private_chunks(self):
        function = exact_method("api/v1/endpoints/generation.py", "_prepared_rag_metadata", {})
        result = function(evidence(), [COLLECTION])
        self.assertEqual(result["collection_ids"], [str(COLLECTION)])
        self.assertEqual(result["validation_report_reference"], evidence()["validation_report_reference"])
        self.assertEqual(result["knowledge_snapshot_id"], evidence()["knowledge_snapshot_id"])
        self.assertNotIn("Synthetic validated", json.dumps(result))

    def test_required_source_gate_never_falls_back_when_scope_or_evidence_empty(self):
        class HTTPError(Exception):
            def __init__(self, status_code, detail): self.status_code = status_code
        function = exact_method("api/v1/endpoints/generation.py", "_require_validated_evidence", {"HTTPException": HTTPError})
        for has_collections in [False, True]:
            with self.assertRaises(HTTPError) as caught: function(True, [], has_collections)
            self.assertEqual(caught.exception.status_code, 422)


class CacheAndPollerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_exact_expiry_is_empty_with_explicit_recovery_diagnostic(self):
        now = datetime(2026, 10, 1, 21, 39, tzinfo=timezone.utc)
        for expiry, expected_count in [(now - timedelta(seconds=1), 0), (now, 0), (now + timedelta(seconds=1), 1)]:
            row = NS(cache_key="context:slug:synthetic-report", expires_at=expiry, context_package=evidence())
            async def execute(statement):
                matched = all(check(row) for check in statement.conditions)
                return NS(scalar_one_or_none=lambda: row if matched else None)
            model = NS(cache_key=Column("cache_key"), expires_at=Column("expires_at"))
            get_cached = exact_method("services/rag_integration.py", "get_cached_context", {
                "select": Statement, "GenerationContextCache": model,
                "datetime": NS(now=lambda _: now), "timezone": timezone,
            }, "ContextCacheService")
            service = type("Cache", (), {"get_cached_context": get_cached})()
            endpoint = exact_method("api/v1/endpoints/internal.py", "get_internal_context", {"success_response": lambda **kw: kw})
            with self.subTest(expiry=expiry), patch.dict(sys.modules, {"app.services.rag_integration": module(context_cache_service=service)}):
                result = await endpoint("synthetic-report", NS(execute=execute))
            self.assertEqual(len(result["data"]["validated_chunks"]), expected_count)
            if not expected_count:
                self.assertEqual(result["data"]["context_status"], "missing_or_expired")
                self.assertNotIn("fallback", result["message"])

    async def test_successful_retry_poller_reaches_storage_using_actual_enum(self):
        # Stop at the first mocked storage query: no long wait or real report.
        class ReachedStorage(BaseException): pass
        enum_tree = ast.parse((APP / "models/enums.py").read_text())
        enum_node = next(node for node in enum_tree.body if isinstance(node, ast.ClassDef) and node.name == "JobStatusType")
        import enum
        enum_ns = {"enum": enum}
        exec(compile(ast.Module(body=[enum_node], type_ignores=[]), "actual_job_enum", "exec"), enum_ns)
        actual_status = enum_ns["JobStatusType"]
        job = NS(id=JOB, document_id=DOC, topic="Synthetic", status=actual_status.pending)
        doc = NS(id=DOC, slug="synthetic-report")
        async def get(model, _): return doc if model is DOC_MODEL else job
        session = NS(get=AsyncMock(side_effect=get), commit=AsyncMock())
        class SessionContext:
            async def __aenter__(self): return session
            async def __aexit__(self, *_): return False
        storage = NS(bucket="synthetic", s3_client=NS(list_objects_v2=Mock(side_effect=ReachedStorage)))
        async def run_sync(function): return function()
        sleep = AsyncMock()
        function = exact_method("services/generation.py", "poll_r2_for_completion", {
            "asyncio": NS(sleep=sleep), "GenerationJob": JOB_MODEL, "JobStatusType": actual_status,
        })
        modules = {"app.database.session": module(async_session_maker=SessionContext),
                   "app.storage.provider": module(storage_provider=storage),
                   "app.models.document": module(Document=DOC_MODEL),
                   "anyio": module(to_thread=NS(run_sync=run_sync))}
        with patch.dict(sys.modules, modules), self.assertRaises(ReachedStorage): await function(JOB)
        self.assertEqual(job.status, actual_status.running)
        session.commit.assert_awaited_once(); sleep.assert_awaited_once_with(15)
        storage.s3_client.list_objects_v2.assert_called_once()


if __name__ == "__main__":
    unittest.main()
