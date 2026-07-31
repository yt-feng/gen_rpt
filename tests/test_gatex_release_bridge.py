import os
import unittest
from unittest.mock import patch

from tools.gatex_release_bridge import ReleaseBridgeError, _checksum_text, _validated_callback_base


class GateXReleaseBridgeTests(unittest.TestCase):
    def test_callback_base_accepts_only_configured_https_origins(self):
        with patch.dict(
            os.environ,
            {"GATEX_GENERATION_CALLBACK_ALLOWED_HOSTS": "gatex.fund,www.gatex.fund"},
            clear=False,
        ):
            self.assertEqual(_validated_callback_base("https://gatex.fund/"), "https://gatex.fund")
            for value in (
                "http://gatex.fund",
                "https://gatex.fund/callback",
                "https://gatex.fund@example.com",
                "https://outside.example",
                "https://gatex.fund:8443",
            ):
                with self.subTest(value=value):
                    with self.assertRaises(ReleaseBridgeError):
                        _validated_callback_base(value)

    def test_release_checksum_must_be_a_full_sha256(self):
        valid = "a" * 64
        self.assertEqual(_checksum_text(valid.upper()), valid)
        for value in ("", "a" * 63, "g" * 64, "sha256:" + valid):
            with self.subTest(value=value):
                with self.assertRaises(ReleaseBridgeError):
                    _checksum_text(value)


if __name__ == "__main__":
    unittest.main()
