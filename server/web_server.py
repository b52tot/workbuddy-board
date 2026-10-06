#!/usr/bin/env python3
"""WorkBuddy Board — HTTP 看板服务。

单端口，纯标准库，零依赖。职责非常单一：
* `GET  /`              返回看板页面（静态资源由 web/ 目录提供）
* `GET  /api/board`     返回看板完整快照（JSON）
* `GET  /api/events`    返回变更流水，支持 since_seq 增量
* `POST /api/task`      创建/更新/移动/删除任务（可选，默认关闭只读）
* `GET  /api/health`    健康探针

设计要点
--------
* **看板只读**：前端永远不改数据，所有写操作都经 MCP 工具。这样「谁改的」
  永远有据可查。若确实需要网页端能拖动卡片，把 `web.allow_write` 置 true，
  后端仍会走同一个 Store，事件照样登记。
* **不引入任何 Web 框架**：单端口 + 无依赖 ⇒ 其他 WorkBuddy 用户克隆即用，
  不必先装 Flask/FastAPI。
* **仅绑定 127.0.0.1**：看板里可能有内部任务标题，默认不对外暴露。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board.config import load_config  # noqa: E402
from board.models import StateError  # noqa: E402
from board.store import Store, StoreError  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
ALLOW_WRITE = False  # 由 config.web.allow_write 覆盖

# /api/events 一次最多返回多少条。没有上限的话，一个
# `?limit=999999999` 就能把整张 events 表拉进内存。
MAX_EVENT_LIMIT = 1000


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkBuddyBoard/1.0"
    store: Store | None = None

    # ---------------------------------------------------------- 工具
    def log_message(self, fmt: str, *args) -> None:
        # 默认 access log 会污染 stdio 场景的 stderr，这里压掉
        pass

    def _cors(self) -> None:
        """CORS 头。

        为什么需要：挂件界面由 pywebview 自己的 HTTP 服务托管
        （端口是随机的），而数据来自本服务的 8791 —— 两者不同源。
        挂件改用**原生 fetch**（而不是走 pywebview 的 IPC）取数，
        因为 IPC 回调在宿主主线程执行，那里同时也是 WebView2 的 UI 线程，
        长耗时的取数会把窗口冻住（实测：窗口"未响应" + JS 卡在加载脚本）。
        既然要跨源，就得允许它。

        只放行本机：服务本身默认也只绑 127.0.0.1，所以 `*` 的风险可控；
        但显式限成 http://127.0.0.1 更稳妥 —— 万一把服务开到 0.0.0.0
        （跨机访问场景），也不会顺手把数据暴露给任意网页。
        """
        origin = self.headers.get("Origin") or ""
        if origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, obj, code: int = 200, extra_headers: dict | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_304(self, etag: str) -> None:
        """304 不能带 body，但必须回 ETag —— 客户端据此确认还是同一版。"""
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json({"error": "not found", "path": str(path)}, 404)
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", "%s; charset=utf-8" % ctype
                         if ctype.startswith("text") or ctype == "application/javascript"
                         else ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------- GET
    def do_OPTIONS(self) -> None:
        """CORS 预检。

        ★ 必须是独立的 do_OPTIONS —— 一度把它写在 do_GET 里，
        结果浏览器预检拿到 501 Unsupported method。
        BaseHTTPRequestHandler 按 `do_<METHOD>` 名字分发，写在 do_GET 里根本不会被调用。

        什么时候会用到：跨源且带自定义头（比如 If-None-Match 做 ETag 条件请求）时，
        浏览器会先发一个 OPTIONS 探路。
        """
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, If-None-Match, If-Modified-Since")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            self._file(WEB_DIR / "index.html")
            return
        if u.path.startswith("/static/"):
            self._file(WEB_DIR / u.path[len("/static/"):])
            return
        if u.path == "/api/health":
            self._json({"ok": True, "server": self.server_version,
                        "db": self.store.db_path,
                        "allow_write": ALLOW_WRITE})
            return
        if u.path == "/api/board":
            snap = self.store.snapshot()
            # ★ ETag 用**内容摘要**算，不是用 server_seq。
            #   原因：stalled（停滞）是随**时间**变化的 —— 同一个 server_seq
            #   下，任务列表没变但停滞标记可能刚翻过来。只认 seq 会漏掉这种
            #   更新，挂件就会一直显示旧状态。
            #   快照约 9KB，算 md5 是几十微秒，可以忽略。
            etag = '"' + hashlib.md5(
                json.dumps(snap, ensure_ascii=False, sort_keys=True,
                           default=str).encode("utf-8")).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                self._send_304(etag)
                return
            self._json(snap, extra_headers={"ETag": etag})
            return
        if u.path == "/api/events":
            # ★ 参数解析必须容错。
            #   原先直接 int(...)：`?limit=abc` 会抛 ValueError，而异常发生在
            #   请求处理线程里、do_GET 没有兜底 ⇒ 客户端看到的是
            #   **连接被单方面断开**（RemoteDisconnected），而不是一个能读懂
            #   的 400。实测踩到：服务本身没事（health 仍 200），但调用方
            #   完全无从判断是自己传错了还是服务挂了。
            try:
                limit = int((q.get("limit") or ["100"])[0])
                since = int((q.get("since_seq") or ["0"])[0])
            except (TypeError, ValueError):
                self._json({"error": "limit / since_seq 必须是整数",
                            "got": {"limit": (q.get("limit") or [None])[0],
                                    "since_seq": (q.get("since_seq") or [None])[0]}}, 400)
                return
            # 上限兜住：没有它，`?limit=999999999` 会把整张 events 表拉进内存
            limit = max(0, min(limit, MAX_EVENT_LIMIT))
            self._json({"events": self.store.list_events(
                limit=limit,
                task_id=(q.get("task_id") or [None])[0],
                since_seq=since)})
            return
        if u.path == "/api/config":
            cfg = load_config()
            self._json({"title": cfg.board.title, "columns": cfg.board.columns,
                        "transitions": cfg.board.transitions,
                        "fields": cfg.board.fields})
            return
        self._json({"error": "not found", "path": u.path}, 404)

    # ---------------------------------------------------------- POST
    def do_POST(self) -> None:
        u = urlparse(self.path)
        if not ALLOW_WRITE:
            self._json({"error": "看板为只读模式；写操作请通过 MCP 工具，"
                                 "或把 config.web.allow_write 置 true"}, 403)
            return
        # 同上：Content-Length 也可能是非法字符串，直接 int() 会把连接炸掉
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._json({"error": "Content-Length 必须是整数",
                        "got": self.headers.get("Content-Length")}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            self._json({"error": "请求体不是合法 JSON: %s" % e}, 400)
            return

        try:
            if u.path == "/api/task":
                action = payload.get("action")
                if action == "create":
                    t = self.store.create_task(
                        title=payload.get("title", ""),
                        column_id=payload.get("column_id"),
                        description=payload.get("description"),
                        tags=payload.get("tags"),
                        fields=payload.get("fields"),
                        actor=payload.get("actor") or "user")
                elif action == "move":
                    t = self.store.move_task(payload["task_id"], payload["to_column"],
                                             before_task_id=payload.get("before_task_id"),
                                             actor=payload.get("actor") or "user")
                elif action == "update":
                    t = self.store.update_task(payload["task_id"],
                                               title=payload.get("title"),
                                               description=payload.get("description"),
                                               tags=payload.get("tags"),
                                               fields=payload.get("fields"),
                                               actor=payload.get("actor") or "user")
                elif action == "delete":
                    self.store.delete_task(payload["task_id"],
                                           actor=payload.get("actor") or "user")
                    self._json({"ok": True, "deleted": payload["task_id"]})
                    return
                else:
                    self._json({"error": "未知 action: %r" % action}, 400)
                    return
                self._json({"ok": True, "task": t.to_dict()})
                return
        except (StoreError, StateError, ValueError) as e:
            self._json({"error": str(e)}, 400)
            return
        except KeyError as e:
            self._json({"error": "缺少字段 %s" % e}, 400)
            return

        self._json({"error": "not found", "path": u.path}, 404)


def main() -> int:
    global ALLOW_WRITE
    cfg = load_config()
    # 直接取属性，不用 getattr 兜底：字段缺失应该在配置校验阶段就暴露，
    # 用 getattr(..., False) 掩盖会把「配置没生效」变成静默行为。
    ALLOW_WRITE = bool(cfg.web.allow_write)
    Handler.store = Store(cfg)

    srv = ThreadingHTTPServer((cfg.web.host, cfg.web.port), Handler)
    url = "http://%s:%d/" % (cfg.web.host, cfg.web.port)
    sys.stderr.write("WorkBuddy Board 看板已启动: %s\n" % url)
    sys.stderr.write("  数据库: %s\n" % Handler.store.db_path)
    sys.stderr.write("  配置源: %s\n" % (cfg.source or "(内置默认值)"))
    sys.stderr.flush()

    if cfg.web.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        Handler.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
