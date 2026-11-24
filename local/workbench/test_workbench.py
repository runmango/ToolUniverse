"""Behavior tests for storage, tool boundaries, and streamed chat persistence."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from workbench.app import app
from workbench.storage import ConversationStore
from workbench.tools import ScientificTools, logical_error


class StoreTests(unittest.TestCase):
    def test_messages_and_rename_survive_reopening_and_delete_cascades(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            store = ConversationStore(path)
            session = store.create("研究问题")
            store.message(session["id"], "user", "查询蛋白", [{"name": "UniProt"}])
            store.update(session["id"], title="蛋白研究", thread_id="thread-1")
            reopened = ConversationStore(path)
            record = reopened.get(session["id"])
            self.assertEqual(record["title"], "蛋白研究")
            self.assertEqual(record["messages"][0]["tools"], [{"name": "UniProt"}])
            reopened.delete(session["id"])
            with reopened.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)


class ToolBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_executor_and_write_tools_are_rejected_before_network_call(self):
        tools = ScientificTools()
        calls = []

        async def fake_info(name):
            return {"name": name, "mcp_annotations": {"readOnlyHint": name != "delete_record", "destructiveHint": False}}

        async def fake_run(name, arguments):
            calls.append(name)
            return {"status": "success"}

        tools.info = fake_info
        tools.run = fake_run
        try:
            for name in ["execute_tool", "python_code_executor", "delete_record"]:
                with self.assertRaises(ValueError):
                    await tools.execute(name, {})
            self.assertEqual(calls, [])
            self.assertEqual((await tools.execute("UniProt_get_entry_by_accession", {"accession": "P05067"}))["status"], "success")
            self.assertEqual(calls, ["UniProt_get_entry_by_accession"])
        finally:
            await tools.client.aclose()

    def test_provider_errors_do_not_look_like_success(self):
        self.assertEqual(logical_error({"status": "error", "error": "timeout"}), "timeout")
        self.assertEqual(logical_error({"success": False, "message": "missing key"}), "missing key")
        self.assertIsNone(logical_error({"status": "success", "data": {"primaryAccession": "P05067"}}))


class FakeBridge:
    executable = "test-fixture"
    login_id = None
    login_result = None

    def __init__(self, connected=True, failed=False):
        self.queues = {}
        self.active_turns = {}
        self.connected = connected
        self.failed = failed

    async def account(self):
        return {"installed": True, "connected": self.connected, "account": {"type": "chatgpt"} if self.connected else None}

    async def thread(self, _thread_id, _model):
        return "test-thread"

    async def rpc(self, method, params, **_kwargs):
        if method == "turn/start":
            queue = self.queues["test-thread"]
            await queue.put({"method": "item/agentMessage/delta", "params": {"itemId": "a", "delta": "测试回答"}})
            await queue.put({"method": "turn/completed", "params": {"turn": {"status": "failed" if self.failed else "completed", "error": {"message": "test upstream failure"} if self.failed else None}}})
            return {"turn": {"id": "test-turn"}}

    async def interrupt(self, _thread_id):
        return None


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def fake_lifespan(application):
            application.state.store = ConversationStore(Path(self.directory.name) / "test.sqlite")
            application.state.bridge = FakeBridge()
            application.state.active_sessions = set()
            yield

        app.router.lifespan_context = fake_lifespan
        self.client = TestClient(app, base_url="http://127.0.0.1:8090")
        self.client.__enter__()
        self.headers = {"X-Workbench-Client": "1"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.router.lifespan_context = self.previous_lifespan
        self.directory.cleanup()

    def test_cross_site_mutations_and_rebound_hosts_are_blocked(self):
        self.assertEqual(self.client.post("/api/sessions", json={"title": "x"}).status_code, 403)
        self.assertEqual(self.client.post("/api/sessions", json={"title": "x"}, headers={**self.headers, "Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "example.com"}).status_code, 421)

    def test_unauthenticated_chat_does_not_save_fake_answer(self):
        app.state.bridge.connected = False
        session = self.client.post("/api/sessions", json={}, headers=self.headers).json()
        response = self.client.post("/api/chat", json={"session_id": session["id"], "text": "hello"}, headers=self.headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(app.state.store.get(session["id"])["messages"], [])

    def test_streamed_response_is_saved_and_lock_released(self):
        session = self.client.post("/api/sessions", json={}, headers=self.headers).json()
        response = self.client.post("/api/chat", json={"session_id": session["id"], "text": "查询"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "done")
        stored = app.state.store.get(session["id"])
        self.assertEqual([message["role"] for message in stored["messages"]], ["user", "assistant"])
        self.assertEqual(stored["messages"][-1]["content"], "测试回答")
        self.assertEqual(stored["messages"][-1]["status"], "completed")
        self.assertNotIn(session["id"], app.state.active_sessions)

    def test_failed_turn_is_not_marked_complete(self):
        app.state.bridge.failed = True
        session = self.client.post("/api/sessions", json={}, headers=self.headers).json()
        self.client.post("/api/chat", json={"session_id": session["id"], "text": "查询"}, headers=self.headers)
        self.assertEqual(app.state.store.get(session["id"])["messages"][-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
