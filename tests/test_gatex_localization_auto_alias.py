"""Automatic source-occurrence checks use synthetic source pages only."""

import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

import fitz
from PIL import Image
from gen_rpt import gatex_pdf_localization as engine

from gen_rpt.gatex_pdf_localization import (
    Block,
    LocalizationError,
    _raster_render_aliases,
    _review_render_aliases,
)


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class Vision:
    provider = "offline-source-review"
    vision_model = "synthetic-vision"

    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def review_render_alias(self, image_bytes, candidate):
        self.calls.append((image_bytes, copy.deepcopy(candidate)))
        value = {
            "verified": True,
            "uncertainRegions": [],
            "occurrences": [{"text": candidate["text"], "bbox": candidate["nativeRegion"]}],
            "nativeOccurrencePresent": True,
            "independentRasterTextPresent": False,
        }
        return self.transform(value, candidate) if self.transform else value


class AutomaticSourceOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.document = fitz.open()
        self.page = self.document.new_page(width=400, height=300)
        background = io.BytesIO()
        Image.new("RGB", (400, 300), (242, 246, 249)).save(background, format="PNG")
        self.page.insert_image(self.page.rect, stream=background.getvalue())
        self.page.insert_text((50, 190), "MEMBER CONFIDENTIAL", fontsize=9)
        self.native = Block("native", 0, "MEMBER CONFIDENTIAL", (50, 180, 165, 192))
        self.raster = Block("raster", 0, self.native.text, (51, 201, 166, 214), kind="raster")
        self.raster.image_sha256 = digest(self.page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes("png"))
        self.blocks = [self.native, self.raster]
        self.records = {block.id: {"text": "会员保密", "retainedEnglish": [], "reviewed": True} for block in self.blocks}
        self.state = {"sourceSha256": digest(self.document.tobytes()), "renderAliasSourceReviews": {}, "renderAliasEvidence": []}
        self.checkpoints = []

    def tearDown(self):
        self.document.close()

    def run_review(self, client, state=None, blocks=None, records=None):
        target = self.state if state is None else state
        _review_render_aliases(self.page, self.blocks if blocks is None else blocks,
                               self.records if records is None else records, target,
                               lambda value: self.checkpoints.append(copy.deepcopy(value)), client)
        return target

    def aliases(self, state=None, blocks=None, records=None):
        state = self.state if state is None else state
        return _raster_render_aliases(self.blocks if blocks is None else blocks,
                                     self.records if records is None else records,
                                     source_sha256=state["sourceSha256"], evidence=state["renderAliasEvidence"])

    def test_unique_source_occurrence_creates_exact_render_evidence_and_private_checkpoint(self):
        client = Vision()
        self.run_review(client)
        self.assertEqual(len(client.calls), 1)
        image, candidate = client.calls[0]
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertEqual(candidate["schema"], "gatex-source-occurrence-candidate/v1")
        self.assertEqual(candidate["sourceSha256"], self.state["sourceSha256"])
        self.assertEqual(candidate["imageSha256"], self.raster.image_sha256)
        self.assertEqual(candidate["sourcePage"], 1)
        self.assertEqual(candidate["rasterBBox"], list(self.raster.bbox))
        self.assertEqual(candidate["nativeBBox"], list(self.native.bbox))
        self.assertEqual(candidate["cropSha256"], digest(image))
        self.assertNotIn("会员保密", json.dumps(candidate, ensure_ascii=False))
        key = digest(json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        saved = self.state["renderAliasSourceReviews"][key]
        self.assertEqual(saved["schema"], "gatex-source-occurrence-review/v1")
        self.assertEqual(saved["candidate"], candidate)
        self.assertEqual(saved["provider"], client.provider)
        self.assertEqual(saved["model"], client.vision_model)
        self.assertTrue(self.checkpoints)
        self.assertEqual(self.aliases(), {"raster": "native"})

    def test_source_review_reuses_pixels_but_regenerates_binding_for_new_equal_reviewed_targets(self):
        client = Vision()
        self.run_review(client)
        keys = set(self.state["renderAliasSourceReviews"])
        for record in self.records.values():
            record["text"] = "会员机密"
        self.run_review(client)
        self.assertEqual(len(client.calls), 1, "unchanged source appearance needs no repeated vision request")
        self.assertEqual(set(self.state["renderAliasSourceReviews"]), keys)
        self.assertEqual(self.state["renderAliasEvidence"][0]["nativeTargetTextSha256"], digest("会员机密"))
        self.assertEqual(self.state["renderAliasEvidence"][0]["rasterTargetTextSha256"], digest("会员机密"))
        self.assertEqual(self.aliases(), {"raster": "native"})

    def test_changed_source_review_prompt_cannot_reuse_the_old_approved_observation(self):
        client = Vision()
        self.run_review(client)
        keys = set(self.state["renderAliasSourceReviews"])
        revised = engine.RENDER_ALIAS_SYSTEM + "\nRequire the revised source-occurrence audit."
        with patch.object(engine, "RENDER_ALIAS_SYSTEM", revised):
            self.run_review(client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(set(self.state["renderAliasSourceReviews"]), keys)
        for saved in self.state["renderAliasSourceReviews"].values():
            self.assertEqual(saved["reviewPromptSha256"], digest(revised))

    def test_changed_source_image_or_original_geometry_cannot_reuse_an_old_source_review(self):
        self.run_review(Vision())
        for changed in ("source", "native-position", "raster-position"):
            with self.subTest(changed=changed):
                state, blocks = copy.deepcopy(self.state), copy.deepcopy(self.blocks)
                state["renderAliasEvidence"] = []
                if changed == "source":
                    state["sourceSha256"] = digest(b"different immutable source")
                else:
                    block = blocks[0 if changed == "native-position" else 1]
                    block.bbox = tuple(value + 0.1 for value in block.bbox)
                client = Vision()
                self.run_review(client, state=state, blocks=blocks)
                self.assertEqual(len(client.calls), 1)

        changed_image = copy.deepcopy(self.blocks)
        changed_image[1].image_sha256 = digest(b"unmatched image pixels")
        client = Vision()
        with self.assertRaises(LocalizationError) as raised:
            self.run_review(client, state=copy.deepcopy(self.state), blocks=changed_image)
        self.assertEqual(raised.exception.code, "UNVERIFIED_RENDER_ALIAS")
        self.assertEqual(client.calls, [], "an image hash absent from the immutable source must fail before vision")

    def test_a_real_second_source_occurrence_is_kept_even_when_the_labels_are_nearby(self):
        def repeated(value, candidate):
            return {**value, "occurrences": [
                {"text": candidate["text"], "bbox": candidate["nativeRegion"]},
                {"text": candidate["text"], "bbox": candidate["rasterRegion"]},
            ], "independentRasterTextPresent": True}
        client = Vision(repeated)
        self.run_review(client)
        self.assertEqual(self.state["renderAliasEvidence"], [])
        self.assertEqual(self.aliases(), {})
        self.run_review(client)
        self.assertEqual(len(client.calls), 1, "a verified two-occurrence source observation is reusable without merging it")

    def test_uncertain_observation_can_advance_to_fresh_verified_evidence_within_the_attempt_bound(self):
        attempts = 0

        def initially_uncertain(value, candidate):
            nonlocal attempts
            attempts += 1
            return {**value, "verified": False, "uncertainRegions": [candidate["rasterRegion"]]} if attempts == 1 else value

        client = Vision(initially_uncertain)
        self.run_review(client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(self.state["renderAliasSourceReviews"]), 1)
        self.assertEqual(len(self.state["renderAliasRejections"]), 1)
        self.assertEqual(self.aliases(), {"raster": "native"})

    def test_three_uncertain_results_stay_rejected_but_do_not_become_a_permanent_cached_verdict(self):
        client = Vision(lambda value, candidate: {**value, "verified": False, "uncertainRegions": [candidate["rasterRegion"]]})
        with self.assertRaises(LocalizationError) as raised:
            self.run_review(client)
        self.assertEqual(raised.exception.code, "UNVERIFIED_RENDER_ALIAS")
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.state["renderAliasSourceReviews"], {})
        self.assertEqual(self.state["renderAliasEvidence"], [])
        self.assertEqual(len(self.state["renderAliasRejections"]), 2)
        next_run = Vision()
        self.run_review(next_run)
        self.assertEqual(len(next_run.calls), 1)
        self.assertEqual(self.aliases(), {"raster": "native"})

    def test_uncertain_or_malformed_source_vision_evidence_cannot_create_an_alias(self):
        for transform in [
            lambda value, candidate: {**value, "verified": False},
            lambda value, candidate: {**value, "uncertainRegions": [candidate["rasterRegion"]]},
            lambda value, candidate: {**value, "occurrences": []},
            lambda value, candidate: {**value, "occurrences": [{"text": "different wording", "bbox": candidate["nativeRegion"]}]},
            lambda value, candidate: {**value, "nativeOccurrencePresent": False},
        ]:
            with self.subTest(transform=transform):
                state = {"sourceSha256": self.state["sourceSha256"], "renderAliasSourceReviews": {}, "renderAliasEvidence": []}
                with self.assertRaises(LocalizationError) as raised:
                    self.run_review(Vision(transform), state=state)
                self.assertEqual(raised.exception.code, "UNVERIFIED_RENDER_ALIAS")
                self.assertEqual(state["renderAliasEvidence"], [])

    def test_multiple_owners_different_targets_and_unreviewed_targets_never_gain_extra_alias_tolerance(self):
        for variant in ("multiple-native", "multiple-raster", "different-target", "unreviewed-target"):
            with self.subTest(variant=variant):
                blocks, records = copy.deepcopy(self.blocks), copy.deepcopy(self.records)
                if variant.startswith("multiple"):
                    extra = copy.deepcopy(blocks[0 if variant == "multiple-native" else 1])
                    extra.id = "second-owner"
                    blocks.append(extra)
                    records[extra.id] = copy.deepcopy(records["native"])
                elif variant == "different-target":
                    records["raster"]["text"] = "另一个译文"
                else:
                    records["raster"]["reviewed"] = False
                client = Vision()
                state = {"sourceSha256": self.state["sourceSha256"], "renderAliasSourceReviews": {}, "renderAliasEvidence": []}
                if variant in {"different-target", "unreviewed-target"}:
                    with self.assertRaises(LocalizationError) as raised:
                        self.run_review(client, state=state, blocks=blocks, records=records)
                    self.assertEqual(raised.exception.code, "RENDER_ALIAS_TARGET_MISMATCH" if variant == "different-target" else "UNVERIFIED_RENDER_ALIAS")
                    self.assertEqual(len(client.calls), 1 if variant == "different-target" else 0)
                else:
                    self.run_review(client, state=state, blocks=blocks, records=records)
                    self.assertEqual(client.calls, [])
                self.assertEqual(self.aliases(state=state, blocks=blocks, records=records), {})

    def test_one_source_occurrence_can_reconcile_different_targets_only_through_a_fresh_unreviewed_draft(self):
        self.records["raster"]["text"] = "会员机密"
        calls = []

        class CorrectingVision(Vision):
            def translate(client, blocks, *, review_drafts, context_blocks, context_records, maximum_attempts):
                calls.append({"blocks": [block.id for block in blocks], "drafts": copy.deepcopy(review_drafts),
                              "context": [block.id for block in context_blocks], "maximum": maximum_attempts})
                candidate = review_drafts["raster"]
                self.assertEqual(candidate, {"text": "会员保密", "retainedEnglish": [], "reviewed": False})
                self.assertEqual(context_records["raster"]["text"], "会员机密")
                return {"raster": {**candidate, "reviewed": True}}

        client = CorrectingVision()
        self.run_review(client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(calls[0]["blocks"], ["raster"])
        self.assertEqual(calls[0]["context"], ["native", "raster"])
        self.assertEqual(calls[0]["maximum"], 3)
        self.assertEqual(self.records["raster"]["text"], self.records["native"]["text"])
        self.assertEqual(self.aliases(), {"raster": "native"})

    def test_a_genuine_second_occurrence_preserves_its_different_reviewed_wording_without_target_rewrite(self):
        self.records["raster"]["text"] = "会员机密"

        class RepeatedVision(Vision):
            def translate(client, *args, **kwargs):
                self.fail("a genuinely repeated source line must not be rewritten as an OCR alias")

        def repeated(value, candidate):
            return {**value, "occurrences": [
                {"text": candidate["text"], "bbox": candidate["nativeRegion"]},
                {"text": candidate["text"], "bbox": candidate["rasterRegion"]},
            ], "independentRasterTextPresent": True}

        self.run_review(RepeatedVision(repeated))
        self.assertEqual(self.records["raster"]["text"], "会员机密")
        self.assertEqual(self.state["renderAliasEvidence"], [])
        self.assertEqual(self.aliases(), {})

    def test_reconciliation_cannot_publish_if_the_new_reviewed_target_still_differs(self):
        self.records["raster"]["text"] = "会员机密"

        class StillDifferent(Vision):
            def translate(client, *args, **kwargs):
                return {"raster": {"text": "会员机密", "retainedEnglish": [], "reviewed": True}}

        with self.assertRaises(LocalizationError) as raised:
            self.run_review(StillDifferent())
        self.assertEqual(raised.exception.code, "RENDER_ALIAS_TARGET_MISMATCH")
        self.assertEqual(self.state["renderAliasEvidence"], [])

    def test_source_casing_or_character_count_is_not_normalized_into_an_occurrence_match(self):
        for text in ("Member Confidential", "MEMBER CONFIDENTIALS"):
            with self.subTest(text=text):
                blocks = copy.deepcopy(self.blocks)
                blocks[1].text = text
                client = Vision()
                self.run_review(client, blocks=blocks)
                self.assertEqual(client.calls, [])
                self.assertEqual(self.state["renderAliasEvidence"], [])


if __name__ == "__main__":
    unittest.main()
