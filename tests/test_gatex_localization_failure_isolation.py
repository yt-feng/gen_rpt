"""Synthetic local failures may not stall independent blocks or approve partial PDFs."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import fitz

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, ModelClient, _translate, digest, translate_pdf,
    _review_protocol_rows, _private_review_protocol_evidence, validate_translation,
)


def verdict(identity):
    return {"id": identity, "complete": True, "faithful": True, "issues": [],
            "professionalChinese": True, "ownedTerminology": True, "qualityIssues": []}


def client():
    with patch.dict("os.environ", {"GATEX_PDF_TRANSLATION_PROVIDER": "apimart", "APIMART_API_KEY": "not-used"}):
        return ModelClient(session_factory=lambda: (_ for _ in ()).throw(AssertionError("network forbidden")), sleep=lambda _: None)


class FailureIsolationTests(unittest.TestCase):
    def test_unsupported_source_block_isolated_before_generation_without_spending_sibling_attempts(self):
        translator=client();calls=[];approved=[];rejected=[]
        translator.accepted_callback=approved.append;translator.rejection_callback=rejected.append
        blocks=[Block("unsupported",0,"Demand is 10 -",None),self.blocks()[1]]
        def once(messages,**kwargs):
            rows=json.loads(messages[1]["content"])["blocks"]
            calls.append([row["id"] for row in rows])
            self.assertEqual(calls[-1],["b"])
            return {"blocks":[verdict("b")]} if "source" in rows[0] else {"blocks":[{"id":"b","text":"供应可能下降。","retainedEnglish":[]}]}
        translator._once=once
        with self.assertRaisesRegex(LocalizationError,"^UNSUPPORTED_QUANTITY$") as failed:
            translator.translate(blocks)
        self.assertEqual(calls,[["b"],["b"]])
        self.assertEqual([set(row) for row in approved],[{"b"}])
        self.assertEqual(failed.exception.block_failures,[{"blockId":"unsupported","sourceHash":blocks[0].source_hash,
            "code":"UNSUPPORTED_QUANTITY","attemptCount":0,"stage":"source_protection"}])
        self.assertFalse(rejected[0]["generationAttempted"])
        calls.clear()
        with self.assertRaisesRegex(LocalizationError,"UNSUPPORTED_QUANTITY"):
            translator.translate(blocks[:1])
        self.assertEqual(calls,[])

    def test_source_protection_failure_continues_all_other_batches_but_cannot_publish_partial(self):
        translator=client();calls=[];state={"blocks":{},"ocr":{}}
        blocks=[Block("unsupported",0,"Demand is 10 -",None),*self.blocks()[1:]]
        def once(messages,**kwargs):
            rows=json.loads(messages[1]["content"])["blocks"]
            if "source" in rows[0]:return {"blocks":[verdict(row["id"]) for row in rows]}
            calls.append([row["id"] for row in rows])
            return {"blocks":[{"id":row["id"],"text":"内容可能变化。","retainedEnglish":[]} for row in rows]}
        translator._once=once
        with patch("gen_rpt.gatex_pdf_localization.MAX_BATCH_BLOCKS",2),self.assertRaisesRegex(LocalizationError,"^TRANSLATION_BLOCKS_PENDING$"):
            _translate(blocks,state,lambda _:None,translator)
        self.assertEqual(calls,[["b","c"],["d"]])
        self.assertEqual(set(state["blocks"]),{"b","c","d"})
        self.assertEqual(state["translationFailures"]["unsupported"]["stage"],"source_protection")
        self.assertEqual(state["translationFailures"]["unsupported"]["attemptCount"],0)
        self.assertEqual(state["translationFailureCounts"],{"UNSUPPORTED_QUANTITY":1})
        self.assertEqual(state["status"],"in-progress")

    def test_unsupported_native_source_cannot_reach_pdf_renderer_and_source_integrity_still_fatal(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/"source.pdf";target=Path(folder)/"target.pdf";doc=fitz.open();page=doc.new_page()
            page.insert_text((40,40),"Demand is 10 -");page.insert_text((40,90),"Supply may fall.");doc.save(source);doc.close()
            translator=client();progress=[];generated=[]
            def once(messages,**kwargs):
                rows=json.loads(messages[1]["content"])["blocks"]
                if "source" in rows[0]:return {"blocks":[verdict(row["id"]) for row in rows]}
                generated.extend(rows)
                return {"blocks":[{"id":row["id"],"text":"供应可能下降。","retainedEnglish":[]} for row in rows]}
            translator._once=once
            with patch("gen_rpt.gatex_pdf_localization._writer",side_effect=AssertionError("partial PDF")):
                with self.assertRaisesRegex(LocalizationError,"^TRANSLATION_BLOCKS_PENDING$"):
                    translate_pdf(source,target,digest(source.read_bytes()),save_progress=progress.append,translation_client=translator)
            self.assertEqual(len(generated),1)
            self.assertEqual(len(progress[-1]["blocks"]),1)
            failure=next(iter(progress[-1]["translationFailures"].values()))
            self.assertEqual((failure["stage"],failure["attemptCount"]),("source_protection",0))
            self.assertFalse(target.exists())
            generated.clear()
            with self.assertRaisesRegex(LocalizationError,"^SOURCE_DIGEST_MISMATCH$"):
                translate_pdf(source,target,"0"*64,translation_client=translator)
            self.assertEqual(generated,[])

    def blocks(self):
        return [Block(identity, 0, source, None) for identity, source in
                [("a", "Demand may rise."), ("b", "Supply may fall."),
                 ("c", "Costs may change."), ("d", "Capacity may expand.")]]

    def test_exhausted_block_keeps_approved_siblings_and_continues_later_batches(self):
        translator = client()
        generated, reviewed, checkpoints = [], [], []
        state = {"blocks": {}, "ocr": {}, "status": "ready", "artifactSha256": "old"}
        def once(messages, **kwargs):
            rows = json.loads(messages[1]["content"])["blocks"]
            if "source" in rows[0]:
                reviewed.append([row["id"] for row in rows])
                return {"blocks": [verdict(row["id"]) for row in rows]}
            generated.append([row["id"] for row in rows])
            return {"blocks": [{"id": row["id"], "text": "尚未译出 FORECAST" if row["id"] == "a" else "内容可能变化。", "retainedEnglish": []} for row in rows]}
        translator._once = once
        with patch("gen_rpt.gatex_pdf_localization.MAX_BATCH_BLOCKS", 2):
            with self.assertRaisesRegex(LocalizationError, "^TRANSLATION_BLOCKS_PENDING$"):
                _translate(self.blocks(), state, lambda s: checkpoints.append(copy.deepcopy(s)), translator)
        self.assertEqual(generated, [["a", "b"], ["a"], ["a"], ["c", "d"]])
        self.assertEqual(reviewed, [["b"], ["c", "d"]])
        self.assertEqual(set(state["blocks"]), {"b", "c", "d"})
        self.assertTrue(all(record["reviewed"] for record in state["blocks"].values()))
        self.assertEqual(state["translationFailures"]["a"]["sourceHash"], self.blocks()[0].source_hash)
        self.assertEqual(state["translationFailures"]["a"]["attemptCount"], 3)
        self.assertEqual(state["translationFailureCounts"], {"UNRESOLVED_ENGLISH": 1})
        self.assertEqual(state["status"], "in-progress")
        self.assertNotIn("artifactSha256", state)
        self.assertEqual(set(checkpoints[-1]["blocks"]), {"b", "c", "d"})
        generated.clear()
        def fixed(messages, **kwargs):
            rows = json.loads(messages[1]["content"])["blocks"]
            if "source" in rows[0]:return {"blocks": [verdict(row["id"]) for row in rows]}
            generated.append([row["id"] for row in rows])
            return {"blocks": [{"id": row["id"], "text": "需求可能增长。", "retainedEnglish": []} for row in rows]}
        translator._once = fixed
        with patch("gen_rpt.gatex_pdf_localization.MAX_BATCH_BLOCKS", 2):
            self.assertEqual(set(_translate(self.blocks(), state, lambda _: None, translator)), {"a", "b", "c", "d"})
        self.assertEqual(generated, [["a"]])
        self.assertEqual(state["translationFailures"], {})
        self.assertNotIn("translationFailureCounts", state)

    def test_provider_error_stops_source_instead_of_spending_on_later_batches(self):
        translator = client()
        calls = []
        def fail(messages, **kwargs):
            calls.append([row["id"] for row in json.loads(messages[1]["content"])["blocks"]])
            raise LocalizationError("MODEL_HTTP_402")
        translator._once = fail
        with patch("gen_rpt.gatex_pdf_localization.MAX_BATCH_BLOCKS", 2):
            with self.assertRaisesRegex(LocalizationError, "MODEL_HTTP_402"):
                _translate(self.blocks(), {"blocks": {}, "ocr": {}}, lambda _: None, translator)
        self.assertEqual(calls, [["a", "b"]] * 3)

    def test_missing_review_only_retries_unchanged_candidate_without_regenerating_sibling(self):
        translator = client()
        generations, reviews, rejected, accepted = [], [], [], []
        translator.rejection_callback = rejected.append
        translator.accepted_callback = accepted.append
        def once(messages, **kwargs):
            payload = json.loads(messages[1]["content"]);rows = payload["blocks"]
            if "source" not in rows[0]:
                generations.append(rows)
                return {"blocks": [{"id": row["id"], "text": "内容可能变化。", "retainedEnglish": []} for row in rows]}
            reviews.append(copy.deepcopy(rows))
            self.assertEqual(len(messages), 2, "independent reviewer sees no earlier verdict or repair instruction")
            self.assertEqual(payload["expectedIds"], [row["id"] for row in rows])
            return {"blocks": [verdict("a")]} if len(reviews) == 1 else {"blocks": [verdict("b")]}
        translator._once = once
        result = translator.translate(self.blocks()[:2])
        self.assertEqual(len(generations), 1)
        self.assertEqual([[r["id"] for r in rows] for rows in reviews], [["a", "b"], ["b"]])
        self.assertEqual(reviews[0][1]["translation"], reviews[1][0]["translation"])
        self.assertEqual([set(rows) for rows in accepted], [{"a"}, {"b"}])
        self.assertEqual(set(result), {"a", "b"})
        self.assertEqual(rejected[0]["reviewProtocol"]["missingIds"], ["b"])
        self.assertEqual(rejected[0]["reviewRequestHashes"][1]["targetTextSha256"], digest(result["b"]["text"]))

    def test_bad_review_protocol_never_approves_and_has_exact_private_diagnostics(self):
        block = self.blocks()[0]
        for response, field, expected in [
            ({"blocks": []}, "missingIds", ["a"]),
            ({"blocks": [verdict("a"), verdict("a")]}, "duplicateIds", {"a": 2}),
            ({"blocks": [verdict("a"), verdict("unrequested")]}, "unknownIds", ["unrequested"]),
            ({"blocks": [{**verdict("a"), "faithful": "true"}]}, "invalidIds", ["a"]),
            ({"blocks": {}}, "blocksFieldType", "dict"),
        ]:
            with self.subTest(field=field):
                translator = client();rejected=[];calls=[]
                translator.rejection_callback = rejected.append
                def once(messages, **kwargs):
                    rows = json.loads(messages[1]["content"])["blocks"]
                    calls.append("review" if "source" in rows[0] else "generation")
                    return response if "source" in rows[0] else {"blocks": [{"id": "a", "text": "需求可能增长。", "retainedEnglish": []}]}
                translator._once = once
                with self.assertRaisesRegex(LocalizationError, "REVIEW_COVERAGE_MISMATCH"):
                    translator.translate([block])
                self.assertEqual(calls, ["generation", "review", "review", "review"])
                self.assertEqual(len(rejected), 3)
                self.assertEqual(rejected[-1]["reviewProtocol"][field], expected)
                self.assertEqual(rejected[-1]["sourceHash"], block.source_hash)
                self.assertFalse(rejected[-1]["normalizedCandidate"]["reviewed"])

    def test_protocol_diagnostic_is_json_safe_bounded_and_excludes_http_fields(self):
        block=self.blocks()[0];candidates={"a":{"text":"需求可能增长。","retainedEnglish":[],"reviewed":True}}
        response={"blocks":[{**verdict("a"),"faithful":float("nan")}],"headers":{"authorization":"never persist"}}
        _,diagnostics=_review_protocol_rows(response,[block])
        record=_private_review_protocol_evidence([block],candidates,response,diagnostics)
        encoded=json.dumps(record,ensure_ascii=False,allow_nan=False)
        self.assertIn("invalidNonFiniteNumber",encoded)
        self.assertNotIn("authorization",encoded)
        self.assertFalse(record["normalizedCandidate"]["reviewed"])
        huge={"blocks":[{"id":"x"*300_000}]}
        _,diagnostics=_review_protocol_rows(huge,[block])
        record=_private_review_protocol_evidence([block],candidates,huge,diagnostics)
        self.assertLessEqual(len(json.dumps(record,ensure_ascii=False,allow_nan=False).encode()),128*1024)
        self.assertTrue(record["reviewResponse"]["truncated"])
        self.assertEqual(record["reviewProtocol"]["missingIds"],["a"])

    def test_malformed_protocol_never_turns_an_adverse_verdict_into_an_unchanged_review_retry(self):
        translator=client();generations=[];reviews=[]
        def once(messages,**kwargs):
            rows=json.loads(messages[1]["content"])["blocks"]
            if "source" not in rows[0]:
                generations.append(messages)
                return {"blocks":[{"id":"a","text":"需求将增长。" if len(generations)==1 else "需求可能增长。","retainedEnglish":[]}]}
            reviews.append(messages)
            self.assertEqual(len(messages),2)
            if len(reviews)==1:return {"blocks":[{**verdict("a"),"faithful":False,"issues":["may changed to will"]},verdict("unknown")]}
            self.assertEqual(rows[0]["translation"],"需求可能增长。")
            return {"blocks":[verdict("a")]}
        translator._once=once
        result=translator.translate(self.blocks()[:1])
        self.assertEqual(len(generations),2)
        self.assertIn("may changed to will",generations[1][-1]["content"])
        self.assertEqual(result["a"]["text"],"需求可能增长。")

    def test_nonempty_issues_remain_adverse_when_true_booleans_have_a_protocol_error(self):
        for field,issue,code in [
            ("issues","missing source qualifier","SEMANTIC_REVIEW_FAILED"),
            ("qualityIssues","unnatural professional Chinese","CHINESE_QUALITY_REVIEW_FAILED"),
        ]:
            for malformed in ("unknown-row","invalid-key"):
                with self.subTest(field=field,malformed=malformed):
                    translator=client();generations=[];reviews=[];rejections=[]
                    translator.rejection_callback=rejections.append
                    def once(messages,**kwargs):
                        rows=json.loads(messages[1]["content"])["blocks"]
                        if "source" not in rows[0]:
                            generations.append(copy.deepcopy(messages))
                            return {"blocks":[{"id":"a","text":"需求可能增长。" if len(generations)==1 else "需求可能上升。","retainedEnglish":[]}]}
                        reviews.append(copy.deepcopy(rows))
                        self.assertEqual(len(messages),2,"fresh reviewer must see no repair feedback")
                        if len(reviews)==1:
                            row={**verdict("a"),field:[issue]}
                            if malformed=="invalid-key":row["professionalChinese"]="true"
                            return {"blocks":[row]+([verdict("unknown")] if malformed=="unknown-row" else [])}
                        self.assertEqual(rows[0]["translation"],"需求可能上升。")
                        return {"blocks":[verdict("a")]}
                    translator._once=once
                    result=translator.translate(self.blocks()[:1])
                    self.assertEqual(len(generations),2)
                    self.assertEqual(len(reviews),2)
                    self.assertIn(issue,generations[1][-1]["content"])
                    self.assertIn(code,generations[1][-1]["content"])
                    self.assertIn("需求可能增长。",generations[1][-1]["content"])
                    self.assertEqual(rejections[0]["reviewResponse"]["blocks"][0][field],[issue])
                    self.assertEqual(result["a"]["text"],"需求可能上升。")

    def test_protocol_issue_cannot_be_washed_by_reviewing_unchanged_or_whitespace_only_candidates(self):
        for field,issue,code in [
            ("issues","missing source detail","SEMANTIC_REVIEW_FAILED"),
            ("qualityIssues","unnatural professional Chinese","CHINESE_QUALITY_REVIEW_FAILED"),
        ]:
            with self.subTest(field=field):
                translator=client();generations=[];reviews=[];rejections=[];approved=[]
                translator.rejection_callback=rejections.append
                translator.accepted_callback=approved.append
                def once(messages,**kwargs):
                    rows=json.loads(messages[1]["content"])["blocks"]
                    if "source" not in rows[0]:
                        generations.append(copy.deepcopy(messages))
                        return {"blocks":[{"id":"a","text":"需 求可能增长。" if len(generations)==3 else "需求可能增长。","retainedEnglish":[]}]}
                    reviews.append(copy.deepcopy(rows))
                    self.assertEqual(len(reviews),1,"unchanged rejected text must not receive another chance at approval")
                    return {"blocks":[{**verdict("a"),field:[issue]},verdict("unknown")]}
                translator._once=once
                with self.assertRaisesRegex(LocalizationError,"^"+code+"$") as failed:
                    translator.translate(self.blocks()[:1])
                self.assertEqual(len(generations),3)
                self.assertEqual(len(reviews),1)
                self.assertEqual(approved,[])
                self.assertEqual(failed.exception.block_failures[0]["code"],code)
                self.assertEqual(failed.exception.block_failures[0]["attemptCount"],3)
                for messages in generations[1:]:
                    self.assertIn(issue,messages[-1]["content"])
                    self.assertIn(code,messages[-1]["content"])
                self.assertEqual([row["code"] for row in rejections[1:]],[code,code])
                self.assertTrue(all(row["review"]["candidateUnchangedAfterAdverseReview"] for row in rejections[1:]))
                json.dumps(rejections,ensure_ascii=False,allow_nan=False)

    def test_counted_noun_remains_translatable_and_cannot_bypass_english_or_numeric_gates(self):
        source="A 1,024-accelerator cluster may expand."
        good={"text":"一个1,024个加速器组成的集群可能扩建。","retainedEnglish":[],"reviewed":True}
        self.assertEqual(validate_translation(source,good)["text"],good["text"])
        with self.assertRaisesRegex(LocalizationError,"UNRESOLVED_ENGLISH"):
            validate_translation(source,{**good,"text":"1,024-accelerator集群可能扩建。"})
        for text in ("accelerator", "024-accelerator"):
            for kind in ("code", "acronym", "unit", "brand"):
                with self.subTest(text=text,kind=kind),self.assertRaisesRegex(LocalizationError,"ORDINARY_ENGLISH_RETAINED"):
                    validate_translation(source,{**good,"text":"1,024-accelerator集群可能扩建。","retainedEnglish":[{"text":text,"kind":kind}]})
        with self.assertRaisesRegex(LocalizationError,"NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(source,{**good,"text":good["text"].replace("1,024","1,023")})

    def test_incomplete_source_never_reaches_renderer_or_creates_a_partial_delivery(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/"source.pdf";target=Path(folder)/"target.pdf";doc=fitz.open();page=doc.new_page()
            page.insert_text((40,40),"Demand may rise.");page.insert_text((40,90),"Supply may fall.");doc.save(source);doc.close()
            translator=client();progress=[]
            def once(messages,**kwargs):
                rows=json.loads(messages[1]["content"])["blocks"]
                if "source" in rows[0]:return {"blocks":[verdict(row["id"]) for row in rows]}
                return {"blocks":[{"id":row["id"],"text":"未译 FORECAST" if "Demand" in row["text"] else "供应可能下降。","retainedEnglish":[]} for row in rows]}
            translator._once=once
            with patch("gen_rpt.gatex_pdf_localization.MAX_BATCH_BLOCKS",1),patch("gen_rpt.gatex_pdf_localization._writer",side_effect=AssertionError("partial render")),patch("gen_rpt.gatex_pdf_localization._review_render_aliases",side_effect=AssertionError("partial alias")):
                with self.assertRaisesRegex(LocalizationError,"TRANSLATION_BLOCKS_PENDING"):
                    translate_pdf(source,target,digest(source.read_bytes()),save_progress=progress.append,translation_client=translator)
            self.assertFalse(target.exists())
            self.assertEqual(len(progress[-1]["blocks"]),1)
            self.assertEqual(progress[-1]["status"],"in-progress")
            self.assertEqual(len(progress[-1]["translationFailures"]),1)


if __name__ == "__main__":
    unittest.main()
