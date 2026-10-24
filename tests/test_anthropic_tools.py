import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import tools


class FakeAnthropicResponse:
    status_code = 200
    text = ""

    def __init__(self, events):
        body = "".join(
            "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            for event in events
        )
        self.raw = body.encode("utf-8")

    def iter_content(self, chunk_size=None):
        # Deliberately split UTF-8 and JSON tokens across network chunks.
        for start in range(0, len(self.raw), 7):
            yield self.raw[start:start + 7]


class InterruptedAnthropicResponse(FakeAnthropicResponse):
    def iter_content(self, chunk_size=None):
        for chunk in super().iter_content(chunk_size):
            yield chunk
        raise chat_engine.requests.ConnectionError("stream reset")


def first_round_events():
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 30}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "先看看要做什么"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "sig-123"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "add_todo", "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"text":"买'}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '草莓"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_2", "name": "read_note", "input": {}}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "usage": {"output_tokens": 4}, "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]


def second_round_events():
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 44}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "都处理好了。"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 5}, "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]


class AnthropicToolsTest(unittest.TestCase):
    @mock.patch("chat_engine._run_anthropic_tool_loop")
    @mock.patch("chat_engine.get_history", return_value=[{"role": "user", "content": "wake"}])
    @mock.patch("chat_engine.get_conversation_summary", return_value="old summary")
    @mock.patch("chat_engine.latest_user_text", return_value="wake")
    @mock.patch("chat_engine.build_system_sections", return_value={"stable": "persona", "volatile": "now"})
    @mock.patch("chat_engine.get_provider")
    def test_format_override_can_route_openai_provider_through_anthropic(
            self, get_provider, _sections, _latest, _summary, _history, anthropic_loop):
        get_provider.return_value = {
            "id": 4, "name": "relay", "base_url": "https://relay.test", "api_key": "key",
            "format": "openai", "cache_mode": "none",
        }
        anthropic_loop.return_value = iter([("delta", "awake")])

        events = list(chat_engine.stream_chat(
            1, provider_id=4, model="claude-test", format_override="claude",
            allow_tools=False,
        ))

        self.assertEqual(events, [("delta", "awake")])
        self.assertEqual(anthropic_loop.call_args.args[6]["format"], "claude")
        sections = anthropic_loop.call_args.args[3]
        summary_block = "【较早对话的累计摘要】\nold summary"
        self.assertEqual(sections["stable_cache_parts"], ["persona", summary_block])
        self.assertNotIn(summary_block, sections["volatile"])
        system = chat_engine._anthropic_system_payload(
            sections, "1h", "anthropic_explicit_sticky"
        )
        self.assertEqual(system[1]["text"], summary_block)
        self.assertEqual(system[1]["cache_control"]["ttl"], "1h")

    def test_small_room_cache_marks_tools_stable_system_and_old_history(self):
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": [{"type": "text", "text": f"turn-{i}"}]}
            for i in range(12)
        ]
        boundary = chat_engine._anthropic_night_history_prefix(messages)
        cached_tools, cached_messages = chat_engine._anthropic_night_cache_payload(
            [{"name": "read_note", "input_schema": {"type": "object"}}],
            messages, "1h", boundary,
        )

        self.assertEqual(boundary, 4)
        self.assertEqual(cached_tools[-1]["cache_control"]["ttl"], "1h")
        self.assertEqual(
            cached_messages[boundary - 1]["content"][-1]["cache_control"]["ttl"],
            "1h",
        )
        self.assertNotIn("cache_control", messages[boundary - 1]["content"][-1])
        self.assertNotIn("cache_control", cached_messages[boundary]["content"][-1])

        system = chat_engine._anthropic_system_payload({
            "stable": "persona\n\nmanual",
            "stable_cache_parts": ["persona", "manual"],
            "volatile": "now",
        }, "1h", "anthropic_explicit_sticky")
        self.assertEqual(
            [bool(block.get("cache_control")) for block in system],
            [True, True, False],
        )

    def test_small_room_history_cache_stops_before_original_image(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "old"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
            {"role": "user", "content": [
                {"type": "text", "text": "photo"},
                {"type": "image", "source": {"type": "base64", "data": "x"}},
            ]},
        ] + [
            {"role": "assistant", "content": [{"type": "text", "text": str(i)}]}
            for i in range(10)
        ]

        self.assertEqual(chat_engine._anthropic_night_history_prefix(messages), 2)

    def test_stored_openai_tool_history_becomes_anthropic_blocks(self):
        history = [
            {"role": "user", "content": "看看便签"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_note", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "今天早点睡"},
            {"role": "assistant", "content": "便签上写着今天早点睡。"},
        ]

        messages = chat_engine._to_anthropic_messages(history)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(messages[1]["content"][0], {
            "type": "tool_use", "id": "call_1", "name": "read_note", "input": {},
        })
        self.assertEqual(messages[2]["content"][0], {
            "type": "tool_result", "tool_use_id": "call_1", "content": "今天早点睡",
        })

    @mock.patch("chat_engine._anthropic_cache_debug")
    @mock.patch("chat_engine._log_http_request")
    @mock.patch("chat_engine.tools.execute")
    @mock.patch("chat_engine.tools.openai_tools")
    @mock.patch("chat_engine.requests.post")
    def test_streaming_tool_loop_preserves_thinking_and_returns_parallel_results(
            self, post, openai_tools, execute, _http_log, _cache_debug):
        post.side_effect = [
            FakeAnthropicResponse(first_round_events()),
            FakeAnthropicResponse(second_round_events()),
        ]
        openai_tools.return_value = [
            {"type": "function", "function": {
                "name": "add_todo", "description": "新增待办",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
            }},
            {"type": "function", "function": {
                "name": "read_note", "description": "读取便签",
                "parameters": {"type": "object", "properties": {}},
            }},
        ]
        execute.side_effect = [
            {"ok": True, "content": "已新增待办：买草莓", "error": "", "duration_ms": 3,
             "source": "builtin", "server": ""},
            {"ok": False, "content": "（便签暂时读不到）", "error": "便签暂时读不到", "duration_ms": 2,
             "source": "builtin", "server": ""},
        ]

        original_history = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "帮我记下买草莓，再看看便签"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "ORIGINAL_IMAGE",
                    },
                },
            ],
        }]
        tool_history = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "帮我记下买草莓，再看看便签"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "TOOL_COPY_IMAGE",
                    },
                },
            ],
        }]

        events = list(chat_engine._run_anthropic_tool_loop(
            "https://relay.example", "secret", "claude-test",
            {"stable": "人设", "volatile": "现在"},
            original_history,
            True, {"id": 1, "name": "Claude", "format": "claude", "cache_mode": "none"},
            7,
            tool_history=tool_history,
        ))

        self.assertEqual(post.call_count, 2)
        self.assertIn(("reasoning", "先看看要做什么"), events)
        self.assertIn(("delta", "都处理好了。"), events)
        starts = [payload for kind, payload in events if kind == "tool_start"]
        self.assertEqual([item["tool"] for item in starts], ["add_todo", "read_note"])
        self.assertEqual(starts[0]["args"], {"text": "买草莓"})
        results = [payload for kind, payload in events if kind == "tool_result"]
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertEqual(len([1 for kind, _payload in events if kind == "usage"]), 2)
        self.assertEqual(_http_log.call_count, 2)
        self.assertTrue(all(call.args[3] for call in _http_log.call_args_list))

        first_payload = post.call_args_list[0].kwargs["json"]
        self.assertEqual(first_payload["tools"][0]["input_schema"]["type"], "object")
        self.assertTrue(first_payload["thinking"]["budget_tokens"] >= 1024)
        self.assertEqual(
            first_payload["messages"][0]["content"][1]["source"]["data"],
            "ORIGINAL_IMAGE",
        )

        second_payload = post.call_args_list[1].kwargs["json"]
        self.assertEqual(
            second_payload["messages"][0]["content"][1]["source"]["data"],
            "TOOL_COPY_IMAGE",
        )
        assistant_blocks = second_payload["messages"][-2]["content"]
        self.assertEqual(assistant_blocks[0]["type"], "thinking")
        self.assertEqual(assistant_blocks[0]["signature"], "sig-123")
        self.assertEqual(assistant_blocks[1]["input"], {"text": "买草莓"})
        result_blocks = second_payload["messages"][-1]["content"]
        self.assertEqual([b["tool_use_id"] for b in result_blocks], ["toolu_1", "toolu_2"])
        self.assertNotIn("is_error", result_blocks[0])
        self.assertTrue(result_blocks[1]["is_error"])

    @mock.patch("chat_engine._anthropic_cache_debug")
    @mock.patch("chat_engine._log_http_request")
    @mock.patch("chat_engine.requests.post")
    def test_empty_200_stream_is_logged_and_emitted_as_failure(
            self, post, http_log, _cache_debug):
        post.return_value = FakeAnthropicResponse([
            {"type": "message_start", "message": {"usage": {"input_tokens": 30}}},
            {"type": "message_stop"},
        ])

        events = list(chat_engine._stream_anthropic_once(
            "https://relay.example", "secret", "claude-test",
            {"stable": "人设", "volatile": "现在"},
            [{"role": "user", "content": [{"type": "text", "text": "你好"}]}],
            False, {"id": 1, "name": "Claude", "format": "claude", "cache_mode": "none"},
            tool_schemas=[],
        ))

        errors = [payload for kind, payload in events if kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("没有收到有效回答事件", errors[0]["message"])
        self.assertFalse(http_log.call_args.args[3])
        self.assertEqual(http_log.call_args.kwargs["status_code"], 200)
        self.assertEqual(http_log.call_args.kwargs["error_type"], "EmptyStream")

    @mock.patch("chat_engine._anthropic_cache_debug")
    @mock.patch("chat_engine._log_http_request")
    @mock.patch("chat_engine.requests.post")
    def test_interrupted_200_stream_is_logged_and_emitted_as_failure(
            self, post, http_log, _cache_debug):
        post.return_value = InterruptedAnthropicResponse([
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "半句话"}},
        ])

        events = list(chat_engine._stream_anthropic_once(
            "https://relay.example", "secret", "claude-test",
            {"stable": "人设", "volatile": "现在"},
            [{"role": "user", "content": [{"type": "text", "text": "你好"}]}],
            False, {"id": 1, "name": "Claude", "format": "claude", "cache_mode": "none"},
            tool_schemas=[],
        ))

        self.assertIn(("delta", "半句话"), events)
        self.assertEqual(len([1 for kind, _payload in events if kind == "error"]), 1)
        self.assertFalse(http_log.call_args.args[3])
        self.assertEqual(http_log.call_args.kwargs["status_code"], 200)
        self.assertEqual(http_log.call_args.kwargs["error_type"], "ConnectionError")



class AnthropicAppIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = {
            "app": (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR),
            "chat": (chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR),
            "tools": tools.DB_PATH,
        }
        db_path = os.path.join(self.root, "nook.db")
        uploads = os.path.join(self.root, "uploads")
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = db_path, self.root, uploads
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = db_path, self.root, uploads
        tools.DB_PATH = db_path
        app.init_db()
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO providers (id,name,format,api_key,api_url,cache,cache_mode,models) "
            "VALUES (1,'Claude','claude','secret','https://relay.example',0,'none','[]')"
        )
        db.commit()
        db.close()
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    @mock.patch("chat_engine.requests.post")
    def test_claude_tools_persist_through_chat_api(self, post):
        post.side_effect = [
            FakeAnthropicResponse(first_round_events()),
            FakeAnthropicResponse(second_round_events()),
        ]

        response = self.client.post("/api/chat/send", json={
            "message": "帮我记下买草莓，再看看便签",
            "provider_id": 1,
            "model": "claude-test",
            "think": True,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_data(as_text=True)
        self.assertIn("都处理好了。", payload)

        messages = self.client.get("/api/chat/messages/1").get_json()
        self.assertEqual(messages[-1]["content"], "都处理好了。")
        self.assertEqual(messages[-1]["reasoning"], "先看看要做什么")
        self.assertNotIn("tool_log", messages[-1])  # 墙后面的工具细节不暴露给聊天 UI
        db = sqlite3.connect(app.DB_PATH)
        stored_tool_log = json.loads(db.execute(
            "SELECT tool_log FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0])
        db.close()
        self.assertEqual(len(stored_tool_log), 2)

        todos = self.client.get("/api/todos").get_json()
        self.assertEqual(todos[0]["text"], "买草莓")
        tidy = self.client.get("/api/tidy").get_json()
        self.assertEqual(len(tidy), 2)
        self.assertTrue(all(item["status"] == "success" for item in tidy))
        self.assertTrue(tidy[0]["run_id"])
        self.assertEqual(len({item["run_id"] for item in tidy}), 1)
        usage = self.client.get("/api/usage").get_json()
        self.assertEqual(len(usage), 2)
        self.assertTrue(all(item["format"] == "claude" for item in usage))


if __name__ == "__main__":
    unittest.main()
