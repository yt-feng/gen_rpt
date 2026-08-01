import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from pypdf import PdfReader

from gen_rpt.gatex_pdf_renderer import (
    release_classification,
    release_pdf_filename,
    render_gatex_release_pdf,
    stable_json_checksum,
)


def sample_release():
    return {
        "itemId": "2182388e-64f3-4d0e-9923-e372cb2cb68c",
        "versionId": "12320741-becc-464a-b4c9-984439803430",
        "versionNo": 2,
        "contentKey": "generated-durable-china-gcc-ai-agenda",
        "title": "The Durable China-GCC AI Investment Agenda",
        "subtitle": "A board-level decision brief.",
        "summary": "Governed deployment platforms create the durable strategic position.",
        "reportType": "Sovereign Capital Strategy",
        "language": "en",
        "accessScope": "member",
        "versionSubmittedAt": "2026-07-31T15:00:00Z",
        "contentSections": [
            {
                "id": "takeaways",
                "kind": "key_takeaways",
                "heading": "Key takeaways",
                "items": [
                    "Prioritize governed deployment platforms.",
                    "Sequence commitments around evidence milestones.",
                    "Preserve explicit cross-border decision rights.",
                ],
            },
            {
                "id": "decision-control",
                "kind": "section",
                "heading": "Deployment control matters more than technology access alone",
                "lead": "Control of integration, data and customer relationships determines durability.",
                "paragraphs": [
                    "The investable edge emerges when technology, local operating access and governance are combined in one mandate.",
                    "Each layer should remain independently replaceable so that the portfolio preserves strategic flexibility.",
                ],
                "evidence": ["The approved evidence base makes localization a recurring condition for access."],
                "so_what": "Reward governed deployment control, not passive vendor exposure.",
            },
            {
                "id": "agenda",
                "kind": "actions",
                "heading": "Move through mandate-specific decision gates",
                "items": [
                    {
                        "horizon": "0-90 days",
                        "action": "Define governance and data boundaries before selecting assets.",
                        "success_metric": "The investment committee approves decision rights.",
                        "rationale": "The approved evidence makes governance control a condition for durable value.",
                    }
                ],
            },
            {
                "id": "methodology",
                "kind": "methodology",
                "heading": "Methodology",
                "body": "This brief uses the approved private-source evidence base.",
            },
        ],
    }


class GatexPdfRendererTests(unittest.TestCase):
    def test_gatex_branding_assets_are_versioned_and_legacy_free(self):
        branding_dir = Path(__file__).resolve().parents[1] / "branding"
        texture_path = branding_dir / "gatex-cover-cloth-v1.jpg"
        mark_path = branding_dir / "gatex-g-mark-white.png"
        theme_path = branding_dir / "theme.json"
        logo_path = branding_dir / "logo.svg"

        texture = fitz.Pixmap(str(texture_path))
        mark = fitz.Pixmap(str(mark_path))
        self.assertGreaterEqual(texture.width, 1_500)
        self.assertGreaterEqual(texture.height, 1_000)
        self.assertEqual((mark.width, mark.height), (600, 600))
        self.assertTrue(mark.alpha)

        theme = json.loads(theme_path.read_text(encoding="utf-8"))
        self.assertEqual(theme["brand_name"], "GateX")
        customer_branding = theme_path.read_text(encoding="utf-8") + logo_path.read_text(encoding="utf-8")
        self.assertNotRegex(customer_branding.lower(), r"blue[ -]?ocean|\bbo\b")
        self.assertIn("gatex-g-mark-white.png", logo_path.read_text(encoding="utf-8"))
        dockerignore = (branding_dir.parent / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("branding/", [line.strip() for line in dockerignore])

    def test_release_file_name_is_versioned_and_deterministic(self):
        payload = sample_release()
        self.assertEqual(
            release_pdf_filename(payload),
            "gatex-durable-china-gcc-ai-agenda-en-v02.pdf",
        )
        self.assertEqual(release_pdf_filename(payload), release_pdf_filename(dict(payload)))

    def test_release_file_name_uses_the_gatex_slug_limit(self):
        payload = {**sample_release(), "contentKey": "generated-" + ("strategic-" * 12)}
        stem = release_pdf_filename(payload).removeprefix("gatex-").removesuffix("-en-v02.pdf")
        self.assertEqual(len(stem), 64)

    def test_release_classification_follows_access_scope(self):
        payload = sample_release()
        self.assertEqual(release_classification(payload), "MEMBER CONFIDENTIAL")
        self.assertEqual(release_classification({**payload, "accessScope": "advanced"}), "PRIVATE OFFICE CONFIDENTIAL")
        self.assertEqual(release_classification({**payload, "accessScope": "staff"}), "GATEX RESTRICTED")

    def test_stable_checksum_matches_canonical_json(self):
        payload = {"z": [3, {"b": 2, "a": "GateX"}], "a": True}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(stable_json_checksum(payload), hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def test_renders_cover_body_furniture_and_metadata(self):
        payload = sample_release()
        with TemporaryDirectory() as directory:
            artifact = render_gatex_release_pdf(payload, Path(directory))
            path = Path(artifact["path"])
            self.assertTrue(path.is_file())
            self.assertEqual(artifact["fileName"], release_pdf_filename(payload))
            self.assertEqual(artifact["contentType"], "application/pdf")
            self.assertGreaterEqual(artifact["pageCount"], 4)
            self.assertTrue(artifact["qa"]["passed"])
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            with fitz.open(path) as document:
                cover_text = document[0].get_text("text")
                full_text = "\n".join(page.get_text("text") for page in document)
                cover_images = document[0].get_images(full=True)
            self.assertIn(payload["summary"], cover_text)
            self.assertNotIn(payload["subtitle"], cover_text)
            self.assertRegex(full_text, r"governance\s+control a condition")
            self.assertGreaterEqual(len(cover_images), 2)

            metadata = PdfReader(str(path)).metadata
            self.assertEqual(metadata.author, "GateX")
            self.assertEqual(metadata.creator, "GateX PDF Release Pipeline")
            visible_brand = " ".join([cover_text, *(str(value or "") for value in metadata.values())]).lower()
            self.assertNotRegex(visible_brand, r"blue[ -]?ocean|\bbo\b")

    def test_renders_chinese_release_with_extractable_cjk_text(self):
        payload = sample_release()
        payload.update(
            {
                "contentKey": "generated-china-gcc-sovereign-capital",
                "title": "中国与海湾主权资本的长期合作议程",
                "subtitle": "面向董事会的跨境投资决策简报",
                "summary": "核心机会来自可治理的本地部署平台，而不是单一技术资产。",
                "reportType": "主权资本战略",
                "language": "zh",
                "contentSections": [
                    {
                        "id": "takeaways",
                        "kind": "key_takeaways",
                        "heading": "关键结论",
                        "items": [
                            "优先投资能够掌握本地化、数据治理和客户关系的部署平台。",
                            "以证据里程碑分阶段配置资本。",
                            "把中美监管差异纳入明确的组合决策权。",
                        ],
                    },
                    {
                        "id": "decision-control",
                        "kind": "section",
                        "heading": "部署控制权比单纯获得技术更重要",
                        "lead": "长期价值取决于谁掌握整合、运营数据与客户关系。",
                        "paragraphs": [
                            "中国供应商提供工程与成本能力，海湾机构提供资本、采购渠道与政策协同。",
                            "投资架构应分离技术供应、本地部署与数据治理，保留战略灵活性。",
                        ],
                        "evidence": ["经审核的证据将本地化列为市场准入的持续条件。"],
                        "so_what": "投资授权应奖励部署体系的控制权，而不是被动的供应商敞口。",
                    },
                    {
                        "id": "methodology",
                        "kind": "methodology",
                        "heading": "方法说明",
                        "body": "本简报仅使用经审核的私有资料和独立核验的公开信息。",
                    },
                ],
            }
        )
        with TemporaryDirectory() as directory:
            artifact = render_gatex_release_pdf(payload, Path(directory))
            self.assertEqual(artifact["fileName"], "gatex-china-gcc-sovereign-capital-zh-v02.pdf")
            with fitz.open(artifact["path"]) as document:
                extracted = "\n".join(page.get_text("text") for page in document)
            self.assertIn("中国与海湾主权资本", extracted)
            self.assertIn("部署控制权比单纯获得技术更重要", extracted)
            self.assertNotIn("\ufffd", extracted)


if __name__ == "__main__":
    unittest.main()
