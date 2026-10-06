"""服务层测试 —— HTTP 契约 + MCP 协议。

重点验的是「对外承诺」：
* HTTP 端点返回的结构稳定（前端据此渲染）
* MCP 工具名规范（不带 mcp__ 前缀）、schema 与 handler 一一对应
* 只读模式下写接口确实被拒（不是「看起来拒了」）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from board.config import Config  # noqa: E402
from board.store import Store  # noqa: E402
from server import mcp_server, web_server  # noqa: E402


def free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cfg = Config()
        cfg.board.db_path = os.path.join(cls.tmp, "api.db")
        cfg.web.port = free_port()
        cfg.validate()

        cls.cfg = cfg
        cls.store = Store(cfg)
        cls.store.create_task(title="t1")
        t2 = cls.store.create_task(title="t2")
        cls.store.move_task(t2.id, "doing")

        web_server.ALLOW_WRITE = False
        web_server.Handler.store = cls.store
        cls.srv = ThreadingHTTPServer((cfg.web.host, cfg.web.port),
                                      web_server.Handler)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://%s:%d" % (cfg.web.host, cfg.web.port)
        time.sleep(0.25)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.store.close()

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_health(self) -> None:
        code, d = self.get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"])
        self.assertFalse(d["allow_write"])

    def test_board_shape(self) -> None:
        code, d = self.get("/api/board")
        self.assertEqual(code, 200)
        for key in ("title", "columns", "tasks_by_column", "stats",
                    "fields_spec", "server_seq", "generated_at"):
            self.assertIn(key, d, "快照缺少 %s，前端会渲染不出来" % key)
        self.assertEqual(d["stats"]["total"], 2)
        self.assertEqual(d["stats"]["finished"], 0)
        self.assertEqual(d["stats"]["percent"], 0.0)
        self.assertIn("todo", d["tasks_by_column"])
        self.assertIn("doing", d["tasks_by_column"])

    def test_index_and_static(self) -> None:
        for p in ("/", "/static/app.js", "/static/style.css"):
            with urllib.request.urlopen(self.base + p, timeout=5) as r:
                self.assertEqual(r.status, 200, p)
                self.assertTrue(r.read(), p)

    def test_events_bad_params_return_400_not_disconnect(self) -> None:
        """★ 非法查询参数必须回**可读的 400**，不能把连接断掉。

        实测踩到过：`?limit=abc` 在 `int()` 处抛 ValueError，而 `do_GET`
        没有兜底 ⇒ 客户端收到 RemoteDisconnected。**服务其实没事**
        （`/api/health` 仍 200），但调用方完全无法区分
        「我自己传错了」和「服务挂了」—— 这类失效最难排查。

        这条同时钉住"不许再退回断连"：若断言失败说明又变成异常逃逸了。
        """
        import urllib.error
        for qs in ("?limit=abc", "?since_seq=xyz"):
            with self.assertRaises(urllib.error.HTTPError,
                                   msg="%s 没有被拒绝，异常又逃逸了" % qs) as cm:
                self.get("/api/events" + qs)
            self.assertEqual(cm.exception.code, 400, qs)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertIn("error", body, "%s 的 400 里没有 error 字段" % qs)

    def test_events_limit_is_clamped(self) -> None:
        """超大 limit 要有上限。

        没有上限时一个 `?limit=999999999` 就能把整张 events 表拉进内存 ——
        不是安全问题，是**可用性**问题（一次请求打满内存）。
        """
        code, d = self.get("/api/events?limit=999999999")
        self.assertEqual(code, 200)
        self.assertLessEqual(len(d["events"]), web_server.MAX_EVENT_LIMIT)

    def test_events_negative_limit_yields_empty(self) -> None:
        """负数 limit 不该报错，也不该变成"全部" —— 取 0 条。"""
        code, d = self.get("/api/events?limit=-5")
        self.assertEqual(code, 200)
        self.assertEqual(d["events"], [])

    def test_events_endpoint(self) -> None:
        code, d = self.get("/api/events?limit=10")
        self.assertEqual(code, 200)
        self.assertIn("events", d)
        self.assertGreaterEqual(len(d["events"]), 3)

    def test_write_refused_when_readonly(self) -> None:
        """只读模式下必须真拒 —— 不能只在 UI 上禁用。"""
        req = urllib.request.Request(
            self.base + "/api/task", method="POST",
            data=json.dumps({"action": "create", "title": "nope"}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("只读模式下写入居然成功了")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)
            body = json.loads(e.read().decode("utf-8"))
            self.assertIn("只读", body["error"])

    def test_unknown_path_404(self) -> None:
        try:
            self.get("/api/__nope__")
            self.fail("未知路径应返回 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


class TestMcpProtocol(unittest.TestCase):
    """直接跑子进程，走真实的 stdio JSON-RPC。"""

    def _run(self, lines: list[dict]) -> list[dict]:
        env = dict(os.environ)
        env["WBB_CONFIG"] = os.path.join(tempfile.mkdtemp(), "cfg.json")
        Path(env["WBB_CONFIG"]).write_text(json.dumps({
            "board": {"db_path": os.path.join(tempfile.mkdtemp(), "mcp.db")}
        }), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, "-m", "server.mcp_server"],
            input="\n".join(json.dumps(x) for x in lines) + "\n",
            capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=40)
        out = []
        for ln in p.stdout.splitlines():
            if ln.strip():
                out.append(json.loads(ln))
        return out

    def test_initialize_and_tools_list(self) -> None:
        res = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        init = [r for r in res if r.get("id") == 1][0]
        self.assertEqual(init["result"]["serverInfo"]["name"], "workbuddy-board")

        tools = [r for r in res if r.get("id") == 2][0]["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn("create_task", names)
        self.assertIn("move_task", names)
        self.assertIn("board_snapshot", names)

        # ★ 关键：工具名不得自带 mcp__ 前缀 —— 前缀由宿主添加
        for n in names:
            self.assertFalse(n.startswith("mcp__"), "工具名不应带 mcp__ 前缀: %s" % n)
            self.assertNotIn("__", n, "工具名不应含双下划线: %s" % n)

        # 每个工具都要有 description 与合法 schema
        for t in tools:
            self.assertTrue(t.get("description"), t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            for r in t["inputSchema"].get("required", []):
                self.assertIn(r, t["inputSchema"]["properties"],
                              "%s 的 required %s 未声明" % (t["name"], r))

    def test_create_move_snapshot_roundtrip(self) -> None:
        res = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "create_task",
                        "arguments": {"title": "写 README", "task_id": "t-readme"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "move_task",
                        "arguments": {"task_id": "t-readme", "to_column": "doing"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "board_snapshot", "arguments": {}}},
        ])
        by_id = {r.get("id"): r for r in res}
        self.assertFalse(by_id[2]["result"].get("isError"), by_id[2])
        self.assertFalse(by_id[3]["result"].get("isError"), by_id[3])

        snap = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertEqual(snap["stats"]["total"], 1)
        self.assertEqual(len(snap["tasks_by_column"]["doing"]), 1)
        self.assertEqual(snap["tasks_by_column"]["doing"][0]["title"], "写 README")

    def test_illegal_move_reports_error_not_silent_success(self) -> None:
        res = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "create_task", "arguments": {"title": "x"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "move_task",
                        "arguments": {"task_id": "nope", "to_column": "doing"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "__ghost_tool__", "arguments": {}}},
        ])
        by_id = {r.get("id"): r for r in res}
        # 不存在的任务必须报错
        self.assertTrue(by_id[3]["result"].get("isError"),
                        "移动不存在的任务必须报错，不能静默成功")
        # 未知工具必须报错且列出可用工具
        self.assertTrue(by_id[4]["result"].get("isError"))
        self.assertIn("create_task", by_id[4]["result"]["content"][0]["text"])

    def test_destructive_tool_requires_confirm(self) -> None:
        res = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "board_reset", "arguments": {}}},
        ])
        r2 = [r for r in res if r.get("id") == 2][0]
        self.assertTrue(r2["result"].get("isError"),
                        "board_reset 未传 confirm 时必须拒绝")


class TestSelftest(unittest.TestCase):
    def test_selftest_passes(self) -> None:
        p = subprocess.run([sys.executable, "-m", "server.mcp_server", "--selftest"],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("SELFTEST OK", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
