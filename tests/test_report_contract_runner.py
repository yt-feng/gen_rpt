"""Cold font discovery must not weaken offline test isolation."""
from contextlib import redirect_stderr, redirect_stdout
import io
import socket
import subprocess
import unittest
from unittest.mock import patch

from tools import run_report_contract_checks as runner


class OfflineRunnerTests(unittest.TestCase):
    def test_fresh_font_manager_uses_mocked_catalog_and_bundled_fonts(self):
        from matplotlib import font_manager
        # Force the real dependency's cache-miss discovery path, even when the
        # process imported pyplot earlier. No subprocess may reach execution.
        font_manager._get_fontconfig_fonts.cache_clear()
        if hasattr(font_manager, "_get_macos_fonts"):
            font_manager._get_macos_fonts.cache_clear()
        with patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")) as process, \
             runner.empty_system_font_catalog():
            fonts = font_manager.FontManager()
        process.assert_not_called()
        self.assertTrue(any(font.name == "DejaVu Sans" for font in fonts.ttflist))

    def test_font_mock_does_not_allow_unknown_commands_or_shell_variants(self):
        for command, kwargs in ((["fc-list", "--unexpected"], {}),
                                ("fc-list --help", {"shell": True}),
                                (["chromium", "--headless"], {})):
            with self.subTest(command=command), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")) as process, \
                 runner.empty_system_font_catalog():
                with self.assertRaisesRegex(AssertionError, "process forbidden"):
                    subprocess.check_output(command, **kwargs)
                process.assert_called_once()

    def test_swallowed_network_or_process_attempt_still_fails_runner(self):
        for effect in (lambda: subprocess.Popen(["forbidden-child"]),
                       lambda: socket.getaddrinfo("invalid.example", 443)):
            def swallowed():
                try:
                    effect()
                except AssertionError:
                    pass
            suite = unittest.TestSuite([unittest.FunctionTestCase(swallowed)])
            with patch.object(unittest.defaultTestLoader, "loadTestsFromNames", return_value=suite), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as captured:
                self.assertEqual(runner.main(), 1)
            self.assertIn("1 external call(s) blocked", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
