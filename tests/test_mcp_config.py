import os
import shutil
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import mcp_client
import tools


class MCPConfigTest(unittest.TestCase):
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
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def _create_server(self):
        response = self.client.post("/api/mcp-servers", json={
            "name": "Ombre Brain",
            "url": "https://brain.example/mcp",
            "narrate": "Morrow在整理思路",
            "access_token": "secret-token",
            "tools": [{
                "name": "reflect",
                "description": "整理思路",
                "inputSchema": {"type": "object", "properties": {}},
            }],
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_secret_is_masked_and_custom_narration_reaches_execution(self):
        server = self._create_server()
        self.assertTrue(server["has_access_token"])
        self.assertNotIn("access_token", server)
        self.assertEqual(server["narrate"], "Morrow在整理思路")

        public_name = tools._mcp_public_name(server["id"], "reflect")
        self.assertEqual(tools.narrate(public_name), "Morrow在整理思路")
        with mock.patch("tools.mcp_client.call_tool", return_value=("整理好了", "http")) as call:
            outcome = tools.execute(public_name, {"topic": "今天"})
        call.assert_called_once_with(
            "https://brain.example/mcp", "reflect", {"topic": "今天"},
            access_token="secret-token",
        )
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["server"], "Ombre Brain")

    def test_discovery_and_updates_preserve_or_clear_saved_secret(self):
        server = self._create_server()
        found = [{"name": "reflect", "description": "整理思路", "inputSchema": {"type": "object"}}]
        with mock.patch("app.mcp_client.list_tools", return_value=(found, "http")) as list_tools:
            response = self.client.post("/api/mcp-servers/discover", json={
                "server_id": server["id"], "url": server["url"], "tools": server["tools"],
            })
        self.assertEqual(response.status_code, 200)
        list_tools.assert_called_once_with(server["url"], access_token="secret-token")

        updated = self.client.put(f"/api/mcp-servers/{server['id']}", json={
            "name": "Ombre Brain", "url": server["url"], "narrate": "Morrow慢慢理清思绪",
            "enabled": True, "transport": "http", "tools": server["tools"],
        }).get_json()
        self.assertTrue(updated["has_access_token"])
        self.assertEqual(updated["narrate"], "Morrow慢慢理清思绪")

        cleared = self.client.put(f"/api/mcp-servers/{server['id']}", json={
            "name": "Ombre Brain", "url": server["url"], "narrate": "",
            "enabled": True, "transport": "http", "tools": server["tools"],
            "clear_access_token": True,
        }).get_json()
        self.assertFalse(cleared["has_access_token"])
        public_name = tools._mcp_public_name(server["id"], "reflect")
        self.assertEqual(tools.narrate(public_name), "Morrow正在请 Ombre Brain 帮忙")

    def test_mcp_client_builds_bearer_header_without_exposing_empty_values(self):
        self.assertEqual(
            mcp_client._request_headers("token-123", Accept="application/json")["Authorization"],
            "Bearer token-123",
        )
        self.assertNotIn("Authorization", mcp_client._request_headers("", Accept="application/json"))
        self.assertTrue(mcp_client._same_origin("https://example.test/mcp", "https://example.test/messages"))
        self.assertFalse(mcp_client._same_origin("https://example.test/mcp", "https://other.test/messages"))

    def test_mcp_client_reads_text_resource(self):
        with mock.patch(
            "mcp_client.call",
            return_value=({"contents": [{"uri": "ombre://recall/cards/0", "text": '{"cards":[]}'}]}, "http"),
        ) as call:
            text, transport = mcp_client.read_resource(
                "https://brain.example/mcp", "ombre://recall/cards/0",
                access_token="secret-token",
            )
        self.assertEqual(text, '{"cards":[]}')
        self.assertEqual(transport, "http")
        call.assert_called_once_with(
            "https://brain.example/mcp", "resources/read",
            {"uri": "ombre://recall/cards/0"}, 30, "secret-token",
        )

    def test_streamable_disconnect_does_not_fall_back_to_legacy_sse(self):
        interrupted = mcp_client.requests.exceptions.ChunkedEncodingError(
            "Response ended prematurely"
        )
        with mock.patch("mcp_client._streamable_call", side_effect=interrupted), \
                mock.patch("mcp_client._legacy_sse_call") as legacy:
            with self.assertRaisesRegex(
                mcp_client.requests.exceptions.ChunkedEncodingError,
                "Response ended prematurely",
            ):
                mcp_client.call("https://brain.example/mcp", "tools/list", {})
        legacy.assert_not_called()

    def test_explicit_streamable_mismatch_can_fall_back_to_legacy_sse(self):
        legacy_result = ({"tools": []}, "sse")
        with mock.patch(
            "mcp_client._streamable_call",
            side_effect=mcp_client.MCPTransportMismatch("HTTP 405: Method Not Allowed"),
        ), mock.patch("mcp_client._legacy_sse_call", return_value=legacy_result) as legacy:
            result = mcp_client.call("https://legacy.example/sse", "tools/list", {})
        self.assertEqual(result, legacy_result)
        legacy.assert_called_once()

    def test_connection_probe_uses_saved_secret_without_refreshing_tools(self):
        server = self._create_server()
        with mock.patch("app.mcp_client.probe", return_value="http") as probe, \
                mock.patch("app.mcp_client.list_tools") as list_tools:
            response = self.client.post("/api/mcp-servers/test-connection", json={
                "server_id": server["id"], "url": server["url"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "transport": "http"})
        probe.assert_called_once_with(server["url"], access_token="secret-token")
        list_tools.assert_not_called()

    def test_streamable_probe_only_initializes_and_releases_session(self):
        init = mock.Mock(
            status_code=200,
            headers={"content-type": "application/json", "Mcp-Session-Id": "session-1"},
        )
        init.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        ready = mock.Mock(status_code=202, text="")
        session = mock.Mock()
        session.post.side_effect = [init, ready]
        with mock.patch("mcp_client.requests.Session", return_value=session):
            transport = mcp_client.probe(
                "https://brain.example/mcp", access_token="secret-token"
            )
        self.assertEqual(transport, "http")
        self.assertEqual(session.post.call_count, 2)
        initialized = session.post.call_args_list[1].kwargs["json"]
        self.assertEqual(initialized["method"], "notifications/initialized")
        session.delete.assert_called_once()
        session.close.assert_called_once()

    def test_chat_visibility_filters_only_mcp_tools(self):
        first = self._create_server()
        second = self.client.post("/api/mcp-servers", json={
            "name": "Tidal", "url": "https://tidal.example/mcp",
            "tools": [{"name": "browse", "description": "翻看", "inputSchema": {"type": "object"}}],
        }).get_json()
        response = self.client.put("/api/chat/tool-visibility", json={
            "server_id": first["id"], "visible": False,
        })
        self.assertEqual(response.status_code, 200)
        servers = self.client.get("/api/chat/tool-visibility").get_json()["servers"]
        visible = {item["name"]: item["visible"] for item in servers}
        self.assertEqual(visible, {"Ombre Brain": False, "Tidal": True})

        filtered = tools.openai_tools(mcp_server_ids=[second["id"]])
        names = {item["function"]["name"] for item in filtered}
        self.assertIn("add_todo", names)
        self.assertTrue(any(name.startswith(f"mcp_{second['id']}_") for name in names))
        self.assertFalse(any(name.startswith(f"mcp_{first['id']}_") for name in names))

    def test_chat_can_hide_one_mcp_tool_without_changing_global_tools(self):
        server = self.client.post("/api/mcp-servers", json={
            "name": "Ombre Brain", "url": "https://brain.example/mcp",
            "tools": [
                {"name": "breath", "title": "Breath"},
                {"name": "hold", "title": "Hold"},
            ],
        }).get_json()
        response = self.client.put("/api/chat/tool-visibility", json={
            "server_id": server["id"], "tool_name": "hold", "visible": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = self.client.get("/api/chat/tool-visibility").get_json()
        item = next(row for row in payload["servers"] if row["id"] == server["id"])
        self.assertEqual(
            {tool["name"]: tool["visible"] for tool in item["tools"]},
            {"breath": True, "hold": False},
        )
        with app.app.app_context():
            chat_scope = app._chat_visible_mcp_server_ids()
        chat_names = {tool["function"]["name"] for tool in tools.openai_tools(
            mcp_server_ids=chat_scope
        )}
        self.assertIn(tools._mcp_public_name(server["id"], "breath"), chat_names)
        self.assertNotIn(tools._mcp_public_name(server["id"], "hold"), chat_names)
        global_names = {tool["function"]["name"] for tool in tools.openai_tools()}
        self.assertIn(tools._mcp_public_name(server["id"], "hold"), global_names)


if __name__ == "__main__":
    unittest.main()
