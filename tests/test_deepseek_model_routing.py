"""Provider request contracts for the direct DeepSeek Flash migration."""
import os
import unittest
from unittest.mock import Mock, patch

from gen_rpt.deepseek_client import DeepSeekClient


def completion():
    response = Mock(status_code=200, headers={})
    response.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "ready"}}]
    }
    return response


class DeepSeekModelRoutingTests(unittest.TestCase):
    def test_saved_v4_model_selections_use_flash_on_direct_provider(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            for model in ("deepseek-v4-pro", "deepseek-pro", "deepseek-v4-flash",
                          "deepseek-v4-pro-0813", "deepseek-pro-0813", "deepseek-v4.1-pro",
                          "deepseek-v4-flash-0813", " DEEPSEEK-V4-PRO "):
                with self.subTest(model=model):
                    client = DeepSeekClient(model=model)
                    self.assertEqual(client.model, "deepseek-flash")
                    self.assertEqual(client.base_url, "https://api.deepseek.com/v1")
                    self.assertFalse(client.use_apimart)

    def test_flash_request_keeps_json_and_explicit_thinking_contract(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with patch("gen_rpt.deepseek_client.requests.post", return_value=completion()) as post:
                client = DeepSeekClient(model="deepseek-flash")
                self.assertEqual(client.chat([{"role": "user", "content": "Return JSON."}],
                                             json_mode=True, max_tokens=1000), "ready")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 1000)

    def test_call_override_cannot_restore_pro(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with patch("gen_rpt.deepseek_client.requests.post", return_value=completion()) as post:
                DeepSeekClient(model="deepseek-chat").chat(
                    [{"role": "user", "content": "ready"}], model="deepseek-v4-pro-0813")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-flash")

    def test_backend_request_receives_normalized_saved_selection(self):
        env = {"DEEPSEEK_API_KEY": "test-key", "BACKEND_URL": "https://backend.example"}
        with patch.dict(os.environ, env, clear=True):
            with patch("gen_rpt.deepseek_client.requests.post", return_value=completion()) as post:
                DeepSeekClient(model="deepseek-v4-pro").chat(
                    [{"role": "user", "content": "ready"}], model="deepseek-pro")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-flash")

    def test_flash_enabled_thinking_retains_requested_effort(self):
        env = {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_THINKING": "enabled",
               "DEEPSEEK_REASONING_EFFORT": "max"}
        with patch.dict(os.environ, env, clear=True):
            with patch("gen_rpt.deepseek_client.requests.post", return_value=completion()) as post:
                DeepSeekClient(model="deepseek-flash").chat([{"role": "user", "content": "ready"}])
        self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "enabled"})
        self.assertEqual(post.call_args.kwargs["json"]["reasoning_effort"], "max")

    def test_invalid_flash_thinking_mode_fails_before_provider_request(self):
        env = {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_THINKING": "invalid"}
        with patch.dict(os.environ, env, clear=True):
            with patch("gen_rpt.deepseek_client.requests.post") as post:
                with self.assertRaises(ValueError):
                    DeepSeekClient(model="deepseek-flash").chat([{"role": "user", "content": "ready"}])
        post.assert_not_called()

    def test_unrelated_legacy_and_apimart_routes_are_preserved(self):
        env = {"DEEPSEEK_API_KEY": "test-key", "APIMART_API_KEY": "test-apimart-key"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(DeepSeekClient(model="deepseek-chat").model, "deepseek-chat")
            self.assertEqual(DeepSeekClient(model="deepseek/deepseek-v4-pro").model,
                             "deepseek/deepseek-v4-pro")
            client = DeepSeekClient(model="gpt-5.6-sol")
            self.assertEqual(client.model, "gpt-5.6-sol")
            self.assertTrue(client.use_apimart)


if __name__ == "__main__":
    unittest.main()
