import unittest

from review_system.extractors.section_parser import parse_report
from review_system.analyzers.citation_analyzer import run


class ReviewBibliographyPromptTests(unittest.TestCase):
    def test_long_report_prompt_keeps_references_section(self):
        report = "# Analysis\n\n" + ("Evidence-backed analysis. " * 1_000)
        report += "\n\n## References\n\n- [Source](https://example.com/source)"

        prompt = parse_report(report).as_prompt_text(max_chars=18_000)

        self.assertLessEqual(len(prompt), 18_000)
        self.assertIn("## References", prompt)
        self.assertIn("https://example.com/source", prompt)
        self.assertTrue(run(None, parse_report(report), {}, combined={})["has_bibliography"])


if __name__ == "__main__":
    unittest.main()
