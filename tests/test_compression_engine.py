import unittest
from unittest import mock

import compression_engine


class CompressionPromptTest(unittest.TestCase):
    @mock.patch("compression_engine._openai_summary", return_value="新的累计摘要")
    @mock.patch("compression_engine._provider")
    def test_bubble_prompt_and_timestamped_transcript(self, provider, openai_summary):
        provider.return_value = {
            "base_url": "https://example.test",
            "api_key": "secret",
            "format": "openai",
        }
        result = compression_engine.summarize(
            "旧摘要",
            [{
                "id": 9,
                "role": "user",
                "content": "后来改成周五见",
                "created_at": "2026-07-26 17:20:00",
            }],
            provider_id=1,
            model="summary-model",
            target_tokens=2000,
        )
        self.assertEqual(result, "新的累计摘要")
        system, user = openai_summary.call_args.args[2:4]
        self.assertIn("Morrow墙后的对话整理助手", system)
        self.assertNotIn("[Summary]", user)
        self.assertIn("2026-07-26 17:20:00 · 你", user)
        self.assertIn("冲突时以较新的信息为准", system)

    @mock.patch("compression_engine._openai_summary", return_value="按自定义规则整理")
    @mock.patch("compression_engine._provider")
    def test_custom_prompt_replaces_default_instead_of_conflicting(self, provider, openai_summary):
        provider.return_value = {"api_key": "secret", "format": "openai"}
        compression_engine.summarize(
            "",
            [{"id": 1, "role": "assistant", "content": "好", "created_at": ""}],
            provider_id=1,
            model="summary-model",
            prompt="这是完整的自定义提示词",
        )
        system = openai_summary.call_args.args[2]
        self.assertEqual(system, "这是完整的自定义提示词")

    @mock.patch("compression_engine.chat_engine._log_http_request")
    @mock.patch("compression_engine.requests.post")
    def test_openai_retries_length_finish_with_more_room(self, post, log_http):
        first = mock.Mock(status_code=200)
        first.json.return_value = {
            "choices": [{"message": {"content": "半截"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 2700},
        }
        second = mock.Mock(status_code=200)
        second.json.return_value = {
            "choices": [{"message": {"content": "完整摘要"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1800},
        }
        post.side_effect = [first, second]
        provider = {
            "id": 5, "name": "玖时", "format": "openai",
            "base_url": "https://example.test", "api_key": "secret",
        }

        result = compression_engine._openai_summary(
            provider, "deepseek-v4-pro", "system", "user", False, 2000
        )

        self.assertEqual(result, "完整摘要")
        self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [4000, 8000])
        self.assertEqual(len(result.usage_items), 2)
        self.assertFalse(log_http.call_args_list[0].args[3])
        self.assertEqual(log_http.call_args_list[0].kwargs["error_type"], "OutputTruncated")
        self.assertTrue(log_http.call_args_list[1].args[3])

    @mock.patch("compression_engine.chat_engine._log_http_request")
    @mock.patch("compression_engine.requests.post")
    def test_repeated_output_limit_is_not_returned_as_a_summary(self, post, _log_http):
        responses = []
        for _ in range(2):
            response = mock.Mock(status_code=200)
            response.json.return_value = {
                "choices": [{"message": {"content": "仍是半截"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 6000},
            }
            responses.append(response)
        post.side_effect = responses
        provider = {
            "id": 5, "name": "玖时", "format": "openai",
            "base_url": "https://example.test", "api_key": "secret",
        }

        with self.assertRaises(compression_engine.CompressionError) as raised:
            compression_engine._openai_summary(
                provider, "deepseek-v4-pro", "system", "user", False, 2000
            )
        self.assertEqual(len(raised.exception.usage_items), 2)
        self.assertIn("没有保存", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
