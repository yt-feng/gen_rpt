"""Source-faithful professional-Chinese policy and cache migration contracts."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, ModelClient, QUALITY_POLICY_VERSION, QUALITY_RULES,
    _audit_cached_quality, _quality_record, _translate, _translation_state,
    _page_blocks, digest, translate_pdf, PROMPT_VERSION, PROGRESS_SCHEMA, OCR_POLICY_VERSION,
    _source_masthead_hint, _block_masthead_hint,
    _source_publication_terms, _fidelity_review_system, QUALITY_AUDIT_SYSTEM,
    RENDER_ALIAS_SYSTEM, RENDER_ALIAS_REVIEW_POLICY,
)


def verdict(identity, *, style=True, terms=True, faithful=True, issues=None):
    return {"id": identity, "complete": True, "faithful": faithful,
        "issues": issues or [], "professionalChinese": style, "ownedTerminology": terms,
        "qualityIssues": [] if style and terms else ["Replace the exact literal calque with natural Chinese, preserving facts."]}


def record(text):
    return {"text": text, "retainedEnglish": [], "reviewed": True}


class QualityTests(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {"GATEX_PDF_TRANSLATION_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "offline-fixture"}):
            return ModelClient(acceptance_mode="strict", sleep=lambda _: None)

    def test_fresh_style_failure_edits_only_candidate_and_rechecks_fidelity(self):
        client = self.client()
        block = Block("b", 0, "Executive Reading: Revenue may grow 7%.", (0, 0, 200, 40))
        calls, rejected, quality = [], [], []
        responses = [
            {"blocks": [{"id": "b", "text": "执行层阅读：收入可能增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]},
            {"blocks": [verdict("b", style=False)]},
            {"blocks": [{"id": "b", "text": "管理层导读：收入可能增长7%。", "retainedEnglish": []}]},
            {"blocks": [verdict("b")]},
        ]
        def once(messages, **_):
            calls.append(copy.deepcopy(messages)); return responses.pop(0)
        client._once = once
        client.rejection_callback = rejected.append
        client.quality_callback = quality.append
        result = client.translate([block])
        self.assertEqual(result["b"]["text"], "管理层导读：收入可能增长7%。")
        edit = json.loads(calls[2][1]["content"])["blocks"][0]
        self.assertEqual(edit["minimalStyleEdit"]["translation"], "执行层阅读：收入可能增长7%。")
        self.assertEqual(edit["text"], block.text)
        self.assertEqual(rejected[0]["code"], "CHINESE_QUALITY_REVIEW_FAILED")
        self.assertTrue(rejected[0]["review"]["faithful"])
        self.assertEqual(quality[0]["b"]["method"], "fresh-independent-review")
        self.assertEqual(len(calls), 4)
        self.assertNotIn("previous", calls[3][0]["content"].lower())

    def test_style_editor_cannot_remove_source_modality_or_pressure_review(self):
        client = self.client()
        block = Block("b", 0, "Revenue may grow 7%.", (0, 0, 200, 40))
        responses = [
            {"blocks": [{"id": "b", "text": "收入或有增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]},
            {"blocks": [verdict("b", style=False)]},
            {"blocks": [{"id": "b", "text": "收入将增长7%。", "retainedEnglish": []}]},
            {"blocks": [verdict("b", faithful=False, issues=["may became definite"])]},
            {"blocks": [{"id": "b", "text": "收入将增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]},
            {"blocks": [verdict("b", faithful=False, issues=["may became definite"])]},
        ]
        calls, saved = [], []
        client._once = lambda messages, **_: (calls.append(copy.deepcopy(messages)) or responses.pop(0))
        client.accepted_callback = saved.append
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate([block])
        self.assertEqual(len(calls), 6)
        self.assertFalse(saved)
        self.assertNotIn("minimalStyleEdit", json.loads(calls[4][1]["content"])["blocks"][0])
        self.assertNotIn("faithful", calls[5][1]["content"])

    def test_missing_quality_fields_cannot_approve_fresh_review(self):
        client = self.client()
        responses = []
        for _ in range(3):
            responses.extend([{ "blocks": [{"id": "b", "text": "需求", "retainedEnglish": []}]},
                {"blocks": [{"id": "b", "complete": True, "faithful": True, "issues": []}]}])
        client._once = lambda *_args, **_kwargs: responses.pop(0)
        with self.assertRaisesRegex(LocalizationError, "REVIEW_COVERAGE_MISMATCH"):
            client.translate([Block("b", 0, "Demand", (0, 0, 50, 20))])

    def test_cached_style_audit_covers_metadata_and_leaves_factual_records_untouched(self):
        client = self.client()
        blocks = [Block("body", 0, "Market evidence", (0, 0, 150, 20)),
                  Block("metadata-title", 0, "Source report title", None, kind="metadata")]
        state = {"blocks": {b.id: {"sourceHash": b.source_hash, **record("市场证据" if b.id == "body" else "来源报告题名")} for b in blocks},
            "ocr": {"unchanged": {"verified": True}}, "status": "complete", "artifactSha256": "old"}
        original = copy.deepcopy(state)
        records = _translate(blocks, state, lambda _: None, client)
        calls = []
        def once(messages, **_):
            calls.append(messages)
            rows = json.loads(messages[1]["content"])["blocks"]
            return {"blocks": [{"id": row["id"], "professionalChinese": True,
                "ownedTerminology": True, "qualityIssues": []} for row in rows]}
        client._once = once
        _audit_cached_quality(blocks, records, state, lambda _: None, client)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["blocks"], original["blocks"])
        self.assertEqual(state["ocr"], original["ocr"])
        self.assertEqual(set(state["qualityReviews"]), {"body", "metadata-title"})
        self.assertTrue(all(row["method"] == "cached-style-audit" for row in state["qualityReviews"].values()))
        self.assertNotIn("complete", calls[0][1]["content"])
        _audit_cached_quality(blocks, records, state, lambda _: None, client)
        self.assertEqual(len(calls), 1, "quality sidecar resumes without re-auditing unchanged text")

    def test_old_cache_style_edit_has_only_two_remaining_fresh_cycles(self):
        client = self.client(); block = Block("b", 0, "Executive Reading", (0, 0, 150, 20))
        old = {"sourceHash": block.source_hash, **record("执行层阅读")}
        state = {"blocks": {"b": copy.deepcopy(old)}, "ocr": {}, "status": "complete"}
        records = _translate([block], state, lambda _: None, client)
        calls = []
        responses = [
            {"blocks": [{"id": "b", "professionalChinese": False, "ownedTerminology": True, "qualityIssues": ["literal calque"]}]},
            {"blocks": [{"id": "b", "text": "执行层阅读", "retainedEnglish": []}]},
            {"blocks": [verdict("b", style=False)]},
            {"blocks": [{"id": "b", "text": "执行层阅读", "retainedEnglish": []}]},
            {"blocks": [verdict("b", style=False)]},
        ]
        client._once = lambda messages, **_: (calls.append(messages) or responses.pop(0))
        with self.assertRaisesRegex(LocalizationError, "CHINESE_QUALITY_REVIEW_FAILED"):
            _audit_cached_quality([block], records, state, lambda _: None, client)
        self.assertEqual(len(calls), 5, "one audit plus two editor/fresh-review cycles, no fourth attempt")
        self.assertEqual(state["blocks"]["b"], old)
        self.assertFalse(state["qualityReviews"])

    def test_old_cache_changed_text_needs_new_fidelity_before_new_sidecar(self):
        client = self.client(); block = Block("b", 0, "Executive Reading", (0, 0, 150, 20))
        state = {"blocks": {"b": {"sourceHash": block.source_hash, **record("执行层阅读")}}, "ocr": {"same": 1}}
        records = _translate([block], state, lambda _: None, client)
        responses = [
            {"blocks": [{"id": "b", "professionalChinese": False, "ownedTerminology": False, "qualityIssues": ["use natural professional heading"]}]},
            {"blocks": [{"id": "b", "text": "管理层导读", "retainedEnglish": []}]},
            {"blocks": [verdict("b")]},
        ]
        client._once = lambda *_args, **_kwargs: responses.pop(0)
        _audit_cached_quality([block], records, state, lambda _: None, client)
        self.assertEqual(state["blocks"]["b"]["text"], "管理层导读")
        self.assertEqual(state["qualityReviews"]["b"]["targetTextSha256"], digest("管理层导读"))
        self.assertEqual(state["qualityReviews"]["b"]["method"], "fresh-independent-review")
        self.assertEqual(state["ocr"], {"same": 1})

    def test_quality_policy_migration_keeps_source_bound_blocks_and_ocr(self):
        client = self.client(); block = Block("b", 0, "Demand", (0, 0, 150, 20))
        state = {"schema": PROGRESS_SCHEMA, "sourceSha256": "a"*64, "promptVersion": PROMPT_VERSION,
            "model": client.model, "translationPolicy": client.translation_policy,
            "blocks": {"b": {"sourceHash": block.source_hash, **record("需求")}},
            "ocr": {"b"*64: {"verified": True, "uncertainRegions": [], "lines": [], "coverage": {"schema": OCR_POLICY_VERSION}}},
            "qualityReviews": {"b": {**_quality_record(block, record("需求"), client, "cached-style-audit"), "schema": "old-style-policy"}},
            "status": "complete", "artifactSha256": "c"*64}
        migrated = _translation_state(state, "a"*64, client.model, False, require_ocr_coverage=True, translation_policy=client.translation_policy)
        self.assertEqual(migrated["blocks"], state["blocks"])
        self.assertEqual(migrated["ocr"], state["ocr"])
        calls=[]
        client._once=lambda messages,**_: (calls.append(messages) or {"blocks":[{"id":"b","professionalChinese":True,"ownedTerminology":True,"qualityIssues":[]}]})
        records = _translate([block], migrated, lambda _:None, client)
        _audit_cached_quality([block], records, migrated, lambda _:None, client)
        self.assertEqual(len(calls),1)
        self.assertEqual(migrated["qualityReviews"]["b"]["schema"],QUALITY_POLICY_VERSION)

    def test_pure_numeric_literal_is_static_policy_not_fabricated_model_review(self):
        client=self.client();block=Block("b",0,"17",(0,0,50,20)); state={"blocks":{},"ocr":{}}
        client._once=lambda *_a,**_k: self.fail("literal policy does not need a model")
        records=_translate([block],state,lambda _:None,client)
        _audit_cached_quality([block],records,state,lambda _:None,client)
        self.assertEqual(state["qualityReviews"]["b"]["method"],"literal-policy")

    def test_policy_preserves_source_domain_and_negation_scope(self):
        for phrase in ("military intelligence", "external", "negation", "third-party", "denying having said P", "Source facts win"):
            self.assertIn(phrase,QUALITY_RULES)
        client=self.client()
        with self.assertRaisesRegex(LocalizationError,"INVALID_REVIEW_ATTEMPT_BUDGET"):
            client.translate([],maximum_attempts=4)

    def test_standalone_military_heading_is_not_a_house_masthead(self):
        heading=Block("h",0,"INTELLIGENCE",(48,84,113,93))
        brand=Block("brand",0,"GATEX",(48,52,127,75))
        self.assertEqual(_source_masthead_hint("INTELLIGENCE"),{})
        self.assertEqual(_block_masthead_hint(heading,[heading]),{})
        self.assertEqual(_block_masthead_hint(heading,[brand,heading])["translation"],"前沿洞察")
        for misplaced in [Block("b",1,"GATEX",brand.bbox),Block("b",0,"GATEX",(48,0,127,20)),
                          Block("b",0,"GATEX",(200,52,270,75)),Block("b",0,"THIRD PARTY",brand.bbox)]:
            self.assertEqual(_block_masthead_hint(heading,[misplaced,heading]),{})
        strategic=Block("s",0,"STRATEGIC INTELLIGENCE",heading.bbox)
        self.assertEqual(_block_masthead_hint(strategic,[brand,strategic])["translation"],"战略洞察")
        self.assertIn("as 战略洞察.",_block_masthead_hint(strategic,[brand,strategic])["instruction"])
        self.assertNotIn("前沿洞察",_block_masthead_hint(strategic,[brand,strategic])["instruction"])
        self.assertEqual(_block_masthead_hint(strategic,[strategic]),{})
        self.assertEqual(_source_publication_terms("A third-party strategic intelligence function."),[])
        self.assertEqual(_source_publication_terms("GateX Strategic Intelligence")[-1]["chineseTerm"],"GateX 战略洞察")

    def test_policy_versions_pin_actual_review_prompts(self):
        # These are release contracts, not snapshots to casually regenerate.
        # Changed policy semantics/prompts require a new version and matching
        # GateX server readiness contract so existing ready files re-enter audit.
        self.assertEqual(QUALITY_POLICY_VERSION,"gatex-professional-zh/v1")
        self.assertEqual(RENDER_ALIAS_REVIEW_POLICY,"gatex-source-occurrence-review/v1")
        self.assertEqual(digest(_fidelity_review_system()),"154e676c3657b694df8acc905ea13c2c0865053169ffb38b5c92adb75bb831e8")
        self.assertEqual(digest(QUALITY_AUDIT_SYSTEM),"c418f7f7aed04c3700fd0e4dc911d789d1e61c5093412be887a033f8f372670f")
        self.assertEqual(digest(RENDER_ALIAS_SYSTEM),"548a391fdf6eccb0bb40ecc6d06519b9548c85618e23f03c8452c2f5e077477e")

    def test_changed_actual_style_prompt_does_not_reuse_old_quality_approval(self):
        client=self.client();block=Block("b",0,"Demand",(0,0,150,20))
        approved=record("需求");state={"blocks":{"b":{"sourceHash":block.source_hash,**approved}},"ocr":{}}
        state["qualityReviews"]={"b":_quality_record(block,approved,client,"cached-style-audit")}
        records=_translate([block],state,lambda _:None,client);calls=[]
        client._once=lambda messages,**_: (calls.append(messages) or {"blocks":[{"id":"b","professionalChinese":True,"ownedTerminology":True,"qualityIssues":[]}]})
        with patch("gen_rpt.gatex_pdf_localization.QUALITY_AUDIT_SYSTEM",QUALITY_AUDIT_SYSTEM+" New professional rule."):
            _audit_cached_quality([block],records,state,lambda _:None,client)
        self.assertEqual(len(calls),1)
        self.assertEqual(state["blocks"]["b"]["text"],"需求")

    def test_changed_source_selected_glossary_reaudits_without_retranslating_facts(self):
        client=self.client();block=Block("b",0,"Executive Reading",(0,0,150,20))
        approved=record("管理层导读");state={"blocks":{"b":{"sourceHash":block.source_hash,**approved}},"ocr":{}}
        state["qualityReviews"]={"b":_quality_record(block,approved,client,"cached-style-audit")}
        records=_translate([block],state,lambda _:None,client);calls=[]
        client._once=lambda messages,**_: (calls.append(messages) or {"blocks":[{"id":"b","professionalChinese":True,"ownedTerminology":True,"qualityIssues":[]}]})
        changed=[{"sourceTerm":"Executive Reading","chineseTerm":"管理层阅读要点"}]
        with patch("gen_rpt.gatex_pdf_localization._source_publication_terms",lambda _:changed):
            _audit_cached_quality([block],records,state,lambda _:None,client)
        self.assertEqual(len(calls),1)
        self.assertEqual(state["blocks"]["b"]["text"],"管理层导读")

    def test_minimal_style_editor_cannot_add_a_currency_identity(self):
        client=self.client();block=Block("b",0,"Revenue may reach $15 million.",(0,0,200,40))
        calls=[]
        responses=[
            {"blocks":[{"id":"b","text":"收入或触及{GATEX_QUANTITY_0}。","retainedEnglish":[]}]},
            {"blocks":[verdict("b",style=False)]},
            {"blocks":[{"id":"b","text":"收入可能达到USD 1500万。","retainedEnglish":[]}]},
            {"blocks":[{"id":"b","text":"收入可能达到USD 1500万。","retainedEnglish":[]}]},
        ]
        client._once=lambda messages,**_: (calls.append(messages) or responses.pop(0))
        with self.assertRaisesRegex(LocalizationError,"NUMERIC_OR_LINK_MISMATCH"):
            client.translate([block])
        self.assertEqual(len(calls),4,"invalid currency is rejected before independent review")

    def test_every_record_reaches_quality_policy_before_pdf_is_publishable(self):
        client=self.client()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"source.pdf";target=Path(folder)/"zh.pdf"
            doc=fitz.open();page=doc.new_page(width=400,height=300)
            page.insert_text((30,50),"Market evidence",fontsize=10)
            page.insert_text((30,100),"17",fontsize=10)
            doc.set_metadata({"title":"Source report title"});doc.set_toc([[1,"Report outline",1]])
            doc.save(source);doc.close()
            source_sha=digest(source.read_bytes())
            with fitz.open(source) as doc:
                blocks,_=_page_blocks(doc[0],0,{"ocr":{}},lambda _:None,lambda _:self.fail("no raster"))
            text_records={block.id:{"sourceHash":block.source_hash,**record("市场证据" if block.text=="Market evidence" else "17")} for block in blocks}
            for identity,source_text,target_text in (("metadata-title","Source report title","来源报告题名"),("toc-1","Report outline","报告目录")):
                text_records[identity]={"sourceHash":digest(source_text),**record(target_text)}
            state={"schema":PROGRESS_SCHEMA,"sourceSha256":source_sha,"promptVersion":PROMPT_VERSION,
                "model":client.model,"translationPolicy":client.translation_policy,"blocks":text_records,"ocr":{},"status":"complete"}
            calls=[];checkpoints=[]
            def once(messages,**_):
                rows=json.loads(messages[1]["content"])["blocks"];calls.extend(row["id"] for row in rows)
                return {"blocks":[{"id":row["id"],"professionalChinese":True,"ownedTerminology":True,"qualityIssues":[]} for row in rows]}
            client._once=once
            result=translate_pdf(source,target,source_sha,state,checkpoints.append,translation_client=client)
            qa=result["qa"]
            self.assertEqual(qa["qualityPolicyVersion"],QUALITY_POLICY_VERSION)
            self.assertEqual(qa["qualityCheckedBlockCount"],4)
            self.assertEqual(qa["qualityCheckedBlockCount"],qa["translatedBlockCount"])
            self.assertTrue(qa["professionalChinesePassed"] and qa["ownedTerminologyPassed"])
            self.assertEqual(set(calls),{blocks[0].id,"metadata-title","toc-1"})
            self.assertEqual(checkpoints[-1]["blocks"],state["blocks"],"unchanged cache audit does not rewrite the source-faithful text")
            self.assertEqual(checkpoints[-1]["qualityReviews"][blocks[1].id]["method"],"literal-policy")
            self.assertEqual(len(checkpoints[-1]["qualityReviews"]),4)


if __name__ == "__main__":
    unittest.main()
